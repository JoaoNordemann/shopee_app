"""
whatsapp_export.py

Monta o "pacote pronto para o WhatsApp" de um produto: legenda de texto
(nome + preço + desconto + link, a partir do TEMPLATE_TEXTO_WHATSAPP) e a
imagem do produto, prontos para colar num chat/canal.

SOBRE A LIMITAÇÃO DO CLIPBOARD (importante):
    pyperclip (que está no requirements.txt) só copia TEXTO — ele não tem
    nenhuma API para colocar uma imagem na área de transferência. Para
    copiar imagem, é necessário usar o QClipboard do próprio Qt (PySide6),
    que sabe lidar com dados binários de imagem (QImage/QPixmap) via
    setImage()/setPixmap().

    Nesta versão, usamos QClipboard também para o texto (não só para a
    imagem), por dois motivos:
    1) Consistência: um único mecanismo de clipboard, sem duas bibliotecas
       concorrendo pelo mesmo recurso do sistema operacional.
    2) Portabilidade: no Linux, pyperclip depende de utilitários externos
       (xclip ou xsel) instalados no sistema — algo que pode não estar
       presente na máquina de quem for rodar o .exe. QClipboard já vem
       junto com o PySide6, que já é dependência obrigatória do projeto.
    pyperclip continua no requirements.txt como fallback de texto para
    cenários sem QApplication rodando (ex: testes fora da UI); se você
    preferir remover essa dependência depois, é seguro fazer isso.

SOBRE "UM BOTÃO OU DOIS" (preciso da sua decisão — ver pergunta ao final
da minha resposta no chat): a maioria dos sistemas operacionais só permite
UM tipo de conteúdo por vez na área de transferência. Ou seja, copiar a
imagem e, em seguida, copiar o texto SOBRESCREVE a imagem — não existe
"colar os dois juntos" de forma atômica no WhatsApp Web a partir de duas
cópias separadas. Por isso este módulo expõe as duas ações de forma
independente (copiar_imagem_para_clipboard / copiar_texto_para_clipboard),
além de uma função de conveniência (copiar_para_area_de_transferencia)
que faz um fluxo "melhor esforço": copia a imagem primeiro (para o
usuário colar no chat) e devolve um status explicando exatamente o que
foi copiado, para a futura UI decidir como avisar o usuário.
"""

from __future__ import annotations

import logging
import random
import sys
from dataclasses import dataclass
from io import BytesIO

import requests
from PIL import Image

from scoring import ProdutoOferta

logger = logging.getLogger(__name__)


class WhatsappExportError(Exception):
    """Erro amigável para qualquer falha ao montar o pacote de exportação."""


# ---------------------------------------------------------------------------
# 1) Legenda de texto
# ---------------------------------------------------------------------------


def _formatar_preco(preco: float | None) -> str:
    """Formata preço em Reais, padrão brasileiro (vírgula decimal)."""
    if preco is None:
        return "0,00"
    try:
        preco = float(preco)
    except (TypeError, ValueError):
        return "0,00"
    # Truque clássico para formatar 1234.5 -> "1.234,50"
    return f"{preco:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _formatar_desconto(desconto_pct: float | None) -> str:
    """Formata desconto sem casas decimais desnecessárias (ex: 30 em vez de 30.0)."""
    if desconto_pct is None:
        return "0"
    try:
        desconto_pct = float(desconto_pct)
    except (TypeError, ValueError):
        return "0"
    if desconto_pct == int(desconto_pct):
        return str(int(desconto_pct))
    return f"{desconto_pct:.1f}"


def escolher_template(templates: list[str]) -> str:
    """
    Sorteia um template aleatório da lista (ex: config.TEMPLATES_TEXTO_WHATSAPP).
    Separado de gerar_legenda() de propósito: gerar_legenda continua pura
    e testável (mesmo template + mesmo produto = mesmo resultado sempre),
    e a aleatoriedade fica isolada aqui, fácil de trocar depois por outra
    estratégia (ex: rotação sequencial em vez de sorteio) se você quiser.

    Levanta ValueError se a lista estiver vazia, em vez de deixar
    random.choice quebrar com um erro menos claro.
    """
    if not templates:
        raise ValueError("Lista de templates de legenda está vazia.")
    return random.choice(templates)


def gerar_legenda(produto: ProdutoOferta, template: str) -> str:
    """
    Preenche `template` (ex: config.TEMPLATE_TEXTO_WHATSAPP) com os dados
    do produto. Valores ausentes recebem fallback razoável em vez de
    quebrar a geração da legenda:
        - nome ausente -> "Produto"
        - preço ausente -> "0,00"
        - desconto ausente -> "0"
        - link ausente -> tenta link_oferta, depois link_produto, depois
          um aviso textual "(link indisponível)"
    """
    nome = produto.nome.strip() if produto.nome else "Produto"
    preco_formatado = _formatar_preco(produto.preco)
    desconto_formatado = _formatar_desconto(produto.desconto_pct)
    link = produto.link_oferta or produto.link_produto or "(link indisponível)"

    try:
        return template.format(
            nome_produto=nome,
            preco=preco_formatado,
            desconto=desconto_formatado,
            link=link,
        )
    except (KeyError, IndexError, ValueError) as exc:
        # Template mal formado (placeholder desconhecido, chave faltando,
        # chaves desbalanceadas "{" sem par etc.) não deve travar o app —
        # cai para um formato mínimo seguro.
        logger.error(
            "Template de legenda inválido (%s). Usando formato mínimo de fallback.",
            exc,
        )
        return f"{nome}\nR$ {preco_formatado} ({desconto_formatado}% OFF)\n{link}"
    except Exception as exc:
        # Rede de segurança final: qualquer outra falha inesperada ao
        # montar a legenda também cai no fallback, nunca escala pro
        # sys.excepthook global por causa de uma ação de "copiar texto".
        logger.exception("Erro inesperado ao gerar legenda: %s", exc)
        return f"{nome}\nR$ {preco_formatado} ({desconto_formatado}% OFF)\n{link}"


# ---------------------------------------------------------------------------
# 2) Download e tratamento de imagem
# ---------------------------------------------------------------------------

TIMEOUT_DOWNLOAD_IMAGEM = 15


def baixar_imagem(url: str) -> bytes:
    """
    Baixa a imagem do produto e devolve os bytes já num formato de imagem
    compatível (JPEG, ou PNG se a imagem original tiver transparência).

    Usamos Pillow para reabrir e "re-salvar" os bytes baixados, garantindo
    que o conteúdo seja de fato uma imagem válida e num formato que o
    QClipboard/QImage vai conseguir interpretar sem problemas — mesmo que
    o servidor tenha mandado um Content-Type inconsistente.
    """
    if not url:
        raise WhatsappExportError("Produto sem URL de imagem definida.")

    try:
        resposta = requests.get(url, timeout=TIMEOUT_DOWNLOAD_IMAGEM)
    except requests.exceptions.Timeout as exc:
        logger.error("Timeout ao baixar imagem de %s: %s", url, exc)
        raise WhatsappExportError(
            "A imagem do produto demorou demais para baixar."
        ) from exc
    except requests.exceptions.RequestException as exc:
        logger.error("Erro de rede ao baixar imagem de %s: %s", url, exc)
        raise WhatsappExportError(
            "Não foi possível baixar a imagem do produto."
        ) from exc

    if resposta.status_code != 200:
        logger.error(
            "HTTP %s ao baixar imagem de %s", resposta.status_code, url
        )
        raise WhatsappExportError(
            f"A imagem do produto retornou erro HTTP {resposta.status_code}."
        )

    try:
        imagem = Image.open(BytesIO(resposta.content))
        imagem.load()  # força a leitura completa, revelando arquivos corrompidos aqui
    except Exception as exc:  # Pillow levanta várias exceções diferentes
        logger.error("Conteúdo baixado de %s não é uma imagem válida: %s", url, exc)
        raise WhatsappExportError(
            "O arquivo baixado não é uma imagem válida."
        ) from exc

    buffer = BytesIO()
    if imagem.mode in ("RGBA", "LA") or "transparency" in imagem.info:
        imagem.save(buffer, format="PNG")
    else:
        if imagem.mode != "RGB":
            imagem = imagem.convert("RGB")
        imagem.save(buffer, format="JPEG", quality=90)

    return buffer.getvalue()


# ---------------------------------------------------------------------------
# 3) Área de transferência (clipboard)
# ---------------------------------------------------------------------------


@dataclass
class StatusCopia:
    """
    Resultado de uma operação de cópia para a área de transferência.
    A UI deve apenas exibir `mensagem` — a decisão de qual texto mostrar
    é feita aqui, não na camada de UI.
    """

    imagem_copiada: bool
    texto_copiado: bool
    mensagem: str


def _obter_qapplication():
    """
    Garante que existe uma QApplication rodando antes de usar QClipboard
    (é um requisito do Qt). Em condições normais, a própria UI do app já
    terá criado essa instância antes de qualquer botão ser clicado — este
    fallback existe só para permitir usar estas funções isoladamente
    (ex: em testes ou scripts).
    """
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError as exc:
        raise WhatsappExportError(
            "PySide6 não está instalado — não é possível acessar a área "
            "de transferência."
        ) from exc

    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def copiar_imagem_para_clipboard(imagem_bytes: bytes) -> bool:
    """
    Copia os bytes de imagem para a área de transferência via QClipboard.
    Retorna True em caso de sucesso, False se não foi possível (ex:
    plataforma sem suporte, ambiente sem GUI, imagem inválida).
    """
    try:
        from PySide6.QtGui import QClipboard, QImage
    except ImportError:
        logger.error("PySide6 indisponível: não é possível copiar imagem.")
        return False

    try:
        _obter_qapplication()
        qimage = QImage()
        if not qimage.loadFromData(imagem_bytes):
            logger.error("Não foi possível interpretar os bytes como QImage.")
            return False

        clipboard: QClipboard = _obter_qapplication().clipboard()
        clipboard.setImage(qimage)
        return True
    except Exception:
        logger.exception("Falha inesperada ao copiar imagem para o clipboard.")
        return False


def copiar_texto_para_clipboard(texto: str) -> bool:
    """
    Copia texto para a área de transferência. Tenta primeiro via
    QClipboard (consistente com a cópia de imagem); se o Qt não estiver
    disponível por algum motivo, cai para pyperclip como último recurso.
    """
    try:
        app = _obter_qapplication()
        app.clipboard().setText(texto)
        return True
    except WhatsappExportError:
        pass  # PySide6 indisponível: tenta o fallback abaixo
    except Exception:
        logger.exception("Falha ao copiar texto via QClipboard.")
        return False

    try:
        import pyperclip

        pyperclip.copy(texto)
        return True
    except Exception:
        logger.exception(
            "Falha ao copiar texto via pyperclip (fallback). No Linux, "
            "verifique se xclip ou xsel está instalado."
        )
        return False


def copiar_para_area_de_transferencia(
    produto: ProdutoOferta, legenda: str
) -> StatusCopia:
    """
    Fluxo de conveniência "melhor esforço": tenta baixar e copiar a
    IMAGEM do produto para o clipboard primeiro (para o usuário colar no
    chat) e retorna um status dizendo exatamente o que foi copiado.

    Importante: como copiar texto depois SOBRESCREVERIA a imagem no
    clipboard, esta função copia SOMENTE a imagem quando ela está
    disponível, e cai para copiar somente o texto quando a imagem falha.
    Ela não tenta copiar as duas coisas em sequência. Para copiar a
    legenda em texto puro em um segundo momento (depois que o usuário já
    colou a imagem), use copiar_texto_para_clipboard(legenda) diretamente
    — é para isso que serve um botão "Copiar texto" separado.
    """
    try:
        imagem_bytes = baixar_imagem(produto.imagem_url)
    except WhatsappExportError as exc:
        logger.warning(
            "Não foi possível baixar a imagem do produto %s: %s",
            produto.item_id,
            exc,
        )
        imagem_bytes = None

    if imagem_bytes is not None:
        imagem_copiada = copiar_imagem_para_clipboard(imagem_bytes)
    else:
        imagem_copiada = False

    if imagem_copiada:
        return StatusCopia(
            imagem_copiada=True,
            texto_copiado=False,
            mensagem=(
                "Imagem copiada! Cole no WhatsApp e depois use o botão "
                "'Copiar texto' para colar a legenda em seguida."
            ),
        )

    # Sem imagem disponível (ou cópia da imagem falhou): melhor esforço é
    # copiar ao menos o texto, para o usuário não ficar de mãos vazias.
    texto_copiado = copiar_texto_para_clipboard(legenda)
    if texto_copiado:
        return StatusCopia(
            imagem_copiada=False,
            texto_copiado=True,
            mensagem=(
                "Não foi possível copiar a imagem, mas a legenda com o "
                "link já está copiada como texto."
            ),
        )

    return StatusCopia(
        imagem_copiada=False,
        texto_copiado=False,
        mensagem=(
            "Não foi possível copiar nem a imagem nem o texto. "
            "Tente novamente."
        ),
    )