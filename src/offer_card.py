"""
offer_card.py

Widget de card de produto (PySide6), usado no grid da MainWindow.

Cuidados de performance com 50+ cards na tela:
    - O download de imagem roda em QThreadPool (não em QThread por card —
      um QRunnable é bem mais leve para tarefas curtas e numerosas como
      essa), com o pool global limitado a poucas threads simultâneas
      (ver _POOL_IMAGENS) para não engargalar a rede nem o disco.
    - Cada imagem é cacheada em disco por item_id (cache/imagens/) — uma
      vez baixada, refazer o card (ex: ao re-filtrar) só lê do disco, sem
      chamar a rede de novo.
    - O card evita efeitos caros: usa QGraphicsDropShadowEffect (sombra
      leve), que é barato, mas fica documentado aqui que, se a sombra
      pesar em telas muito grandes de cards, a primeira coisa a cortar é
      esse efeito.

NOTA sobre reconstrução do grid: hoje a MainWindow recria os OfferCard a
cada mudança de filtro (busca/categoria/score). Isso é simples e correto,
mas refaz o layout dos widgets toda hora — a imagem não é rebaixada (cache
em disco resolve isso), mas o widget em si é recriado. Se no futuro isso
pesar com muitos produtos, dá para evoluir para "esconder/mostrar" cards
já existentes em vez de destruir e recriar. Por ora, não implementei essa
otimização para manter o código simples.
"""

from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPixmap
from shiboken6 import isValid
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

import config
from scoring import ProdutoOferta, calcular_score_final
from whatsapp_export import (
    WhatsappExportError,
    baixar_imagem,
    copiar_imagem_para_clipboard,
    copiar_texto_para_clipboard,
    escolher_template,
    gerar_legenda,
)

LARGURA_CARD = 220
ALTURA_IMAGEM = 160
DURACAO_FEEDBACK_MS = 2000

# Pool global compartilhado por todos os cards — evita criar uma
# QThreadPool nova por card e limita downloads simultâneos.
_POOL_IMAGENS = QThreadPool.globalInstance()
_POOL_IMAGENS.setMaxThreadCount(6)


# ---------------------------------------------------------------------------
# Carregamento de imagem em background (QRunnable + sinais)
# ---------------------------------------------------------------------------


class _SinaisCarregamentoImagem(QObject):
    concluido = Signal(bytes)
    falhou = Signal(str)


class _TarefaCarregarImagem(QRunnable):
    """
    Carrega a imagem do produto: lê do cache em disco se existir, senão
    baixa via whatsapp_export.baixar_imagem() (que já trata timeout,
    erros de rede e normaliza o formato) e salva no cache para a próxima
    vez.
    """

    def __init__(self, url_imagem: str, caminho_cache: Path):
        super().__init__()
        self.url_imagem = url_imagem
        self.caminho_cache = caminho_cache
        self.sinais = _SinaisCarregamentoImagem()

    def run(self) -> None:
        try:
            if self.caminho_cache.exists():
                dados = self.caminho_cache.read_bytes()
            else:
                dados = baixar_imagem(self.url_imagem)
                self.caminho_cache.parent.mkdir(parents=True, exist_ok=True)
                self.caminho_cache.write_bytes(dados)
        except WhatsappExportError as exc:
            self.sinais.falhou.emit(str(exc))
            return
        except OSError as exc:
            self.sinais.falhou.emit(f"Erro ao ler/salvar cache de imagem: {exc}")
            return

        self.sinais.concluido.emit(dados)


# ---------------------------------------------------------------------------
# Cores do badge de score
# ---------------------------------------------------------------------------


TAMANHO_BADGE_SCORE = 56


def _estilo_badge_score() -> str:
    """
    Selo único (elegante, neutro), sem o antigo esquema semáforo
    verde/amarelo/vermelho — decisão de design pedida explicitamente:
    borda rosa pastel + fundo branco/rosa muito pálido, sempre no mesmo
    estilo, independente do valor do score. Quem quiser recuperar a
    indicação rápida de severidade por cor pode pedir de volta depois.
    """
    return (
        "background-color: #FFF5F8; color: #E75480; "
        f"border: 2px solid #FFB6C1; border-radius: {TAMANHO_BADGE_SCORE // 2}px; "
        "font-weight: 700; font-size: 11pt;"
    )


# ---------------------------------------------------------------------------
# OfferCard
# ---------------------------------------------------------------------------


class OfferCard(QWidget):
    """
    Card de um produto no grid. Recebe o produto já com score calculado
    (ver data_service.py) e, se o usuário editar Utilidade/Facilidade de
    conteúdo manualmente, recalcula o score SÓ deste card, sem precisar
    de um "Atualizar" geral.
    """

    def __init__(
        self,
        produto: ProdutoOferta,
        templates_legenda: list[str],
        fontes_auxiliares: dict | None = None,
        on_score_changed=None,  # Callable[[ProdutoOferta], None] | None
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.produto = produto
        self.templates_legenda = templates_legenda
        self.fontes_auxiliares = fontes_auxiliares or {}
        self.on_score_changed = on_score_changed

        self._imagem_bytes: bytes | None = None
        self._acao_pendente_apos_imagem: str | None = None  # "copiar" | None

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("offerCard")
        self.setFixedWidth(LARGURA_CARD)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        self._aplicar_estilo()
        self._montar_ui()
        self._iniciar_carregamento_imagem()

    # ------------------------------------------------------------------
    # Estilo visual
    # ------------------------------------------------------------------

    def _aplicar_estilo(self) -> None:
        self.setStyleSheet(
            """
            #offerCard {
                background-color: #FFFDFE;
                border: 1px solid #F5E1E8;
                border-radius: 18px;
            }
            #offerCard QLabel {
                color: #4A4A4A;
                background-color: transparent;
            }
            #offerCard QPushButton {
                border-radius: 9px;
                padding: 6px 8px;
                background-color: #FFF5F8;
                border: 1.5px solid #FFB6C1;
                color: #E75480;
                font-weight: 600;
                font-size: 8.5pt;
            }
            #offerCard QPushButton:hover {
                background-color: #FFE1E9;
                border: 1.5px solid #FFA6BE;
            }
            #offerCard QPushButton:pressed {
                background-color: #FFD1DC;
            }
            #offerCard QPushButton:disabled {
                color: #D9C3CB;
                background-color: #FCF7F9;
                border: 1.5px solid #F0DCE3;
            }
            #offerCard QDoubleSpinBox {
                background-color: #ffffff;
                color: #4A4A4A;
                border: 1px solid #F3D9E3;
                border-radius: 6px;
                padding: 2px 4px;
            }
            """
        )

        # Sombra suave com leve tonalidade rosa (em vez de cinza puro) —
        # dá o efeito "glass" pedido, sem pesar visualmente.
        sombra = QGraphicsDropShadowEffect(self)
        sombra.setBlurRadius(28)
        sombra.setXOffset(0)
        sombra.setYOffset(6)
        sombra.setColor(QColor(255, 182, 193, 70))
        self.setGraphicsEffect(sombra)

    # ------------------------------------------------------------------
    # Montagem da UI
    # ------------------------------------------------------------------

    def _montar_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        layout.addWidget(self._criar_label_imagem())
        layout.addWidget(self._criar_label_nome())
        layout.addLayout(self._criar_linha_preco_desconto())
        layout.addLayout(self._criar_linha_comissao_rating())
        layout.addWidget(self._criar_badge_score())
        layout.addLayout(self._criar_linha_utilidade_facilidade())
        layout.addLayout(self._criar_linha_botoes())

    def _criar_label_imagem(self) -> QLabel:
        self.label_imagem = QLabel("Carregando...")
        self.label_imagem.setFixedSize(LARGURA_CARD - 32, ALTURA_IMAGEM)
        self.label_imagem.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label_imagem.setStyleSheet(
            "background-color: #FFF5F8; border-radius: 12px; color: #D9AEBB;"
        )
        self.label_imagem.setScaledContents(False)
        return self.label_imagem

    def _criar_label_nome(self) -> QLabel:
        self.label_nome = QLabel()
        self.label_nome.setToolTip(html.escape(self.produto.nome))
        self.label_nome.setWordWrap(False)
        self._atualizar_texto_nome_truncado()
        return self.label_nome

    def _atualizar_texto_nome_truncado(self) -> None:
        metrics = QFontMetrics(self.label_nome.font())
        largura_disponivel = LARGURA_CARD - 32
        texto_truncado = metrics.elidedText(
            self.produto.nome, Qt.TextElideMode.ElideRight, largura_disponivel
        )
        # Sanitização: escapa <, >, & antes de virar texto do QLabel. Sem
        # isso, um nome de produto "sujo" vindo da API contendo esses
        # caracteres poderia ser interpretado como HTML pelo QLabel
        # (autodetecção de rich text) e quebrar o layout do card.
        # setTextFormat(RichText) explícito garante que as entidades
        # escapadas (&amp;, &lt; etc.) sejam exibidas como os caracteres
        # originais, e não como texto literal "&amp;".
        self.label_nome.setTextFormat(Qt.TextFormat.RichText)
        self.label_nome.setText(html.escape(texto_truncado))
        self.label_nome.setStyleSheet(
            "color: #4A4A4A; font-weight: 600; font-size: 11pt; "
            'font-family: "Poppins", "Montserrat", "Segoe UI"; '
            "background-color: transparent;"
        )

    def _criar_linha_preco_desconto(self) -> QHBoxLayout:
        linha = QHBoxLayout()
        preco_texto = f"R$ {self.produto.preco:.2f}".replace(".", ",")
        self.label_preco = QLabel(preco_texto)
        self.label_preco.setStyleSheet(
            "color: #4A4A4A; font-weight: 700; font-size: 12pt; "
            "background-color: transparent;"
        )
        self.label_desconto = QLabel(f"{self.produto.desconto_pct:.0f}% OFF")
        self.label_desconto.setStyleSheet(
            "color: #E75480; font-weight: 700; background-color: transparent;"
        )
        linha.addWidget(self.label_preco)
        linha.addStretch()
        linha.addWidget(self.label_desconto)
        return linha

    def _criar_linha_comissao_rating(self) -> QHBoxLayout:
        linha = QHBoxLayout()
        self.label_comissao = QLabel(f"Comissão: {self.produto.comissao_pct:.0f}%")
        self.label_comissao.setStyleSheet(
            "color: #9B8791; font-size: 9pt; background-color: transparent;"
        )
        self.label_rating = QLabel(f"★ {self.produto.rating:.1f}")
        self.label_rating.setStyleSheet(
            "color: #D4A373; font-weight: 700; background-color: transparent;"
        )
        linha.addWidget(self.label_comissao)
        linha.addStretch()
        linha.addWidget(self.label_rating)
        return linha

    def _criar_badge_score(self) -> QWidget:
        container = QHBoxLayout()
        container.addStretch()

        legenda = QLabel("Score")
        legenda.setStyleSheet(
            "color: #9B8791; font-size: 8.5pt; font-weight: 500; "
            "background-color: transparent;"
        )
        container.addWidget(legenda)
        container.addSpacing(6)

        self.label_score = QLabel()
        self.label_score.setFixedSize(TAMANHO_BADGE_SCORE, TAMANHO_BADGE_SCORE)
        self.label_score.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._atualizar_badge_score()
        container.addWidget(self.label_score)

        container.addStretch()

        wrapper = QWidget()
        wrapper.setLayout(container)
        return wrapper

    def _atualizar_badge_score(self) -> None:
        score = self.produto.score_final or 0.0
        self.label_score.setText(f"{score:.1f}")
        self.label_score.setStyleSheet(_estilo_badge_score())

    def _criar_linha_utilidade_facilidade(self) -> QHBoxLayout:
        """
        Campos editáveis manuais (decisão do projeto: opção "a" — edição
        manual direto no card). Adicionei também o campo de Facilidade de
        conteúdo, além de Utilidade que você pediu explicitamente nesta
        etapa — os dois critérios foram decididos juntos como "editável
        manual" lá no início do projeto, e scoring.py já espera os dois
        campos (utilidade_manual e facilidade_conteudo_manual) para
        calcular o score corretamente.
        """
        linha = QHBoxLayout()

        linha.addWidget(QLabel("Utilidade:"))
        self.spin_utilidade = self._criar_spin_manual(self.produto.utilidade_manual)
        self.spin_utilidade.valueChanged.connect(self._ao_editar_utilidade)
        linha.addWidget(self.spin_utilidade)

        linha.addWidget(QLabel("Facilid.:"))
        self.spin_facilidade = self._criar_spin_manual(
            self.produto.facilidade_conteudo_manual
        )
        self.spin_facilidade.valueChanged.connect(self._ao_editar_facilidade)
        linha.addWidget(self.spin_facilidade)

        return linha

    def _criar_spin_manual(self, valor_atual: float | None) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(0.0, 10.0)
        spin.setSingleStep(0.5)
        spin.setDecimals(1)
        # Bloqueia sinais ao definir o valor inicial: não queremos que
        # simplesmente exibir o card já dispare um recálculo de score.
        spin.blockSignals(True)
        spin.setValue(valor_atual if valor_atual is not None else 5.0)
        spin.blockSignals(False)
        return spin

    def _criar_linha_botoes(self) -> QHBoxLayout:
        linha = QHBoxLayout()
        linha.setSpacing(8)

        self.botao_copiar_imagem = QPushButton("📷  Imagem")
        self.botao_copiar_imagem.clicked.connect(self._ao_clicar_copiar_imagem)
        linha.addWidget(self.botao_copiar_imagem)

        self.botao_copiar_texto = QPushButton("📋  Texto")
        self.botao_copiar_texto.clicked.connect(self._ao_clicar_copiar_texto)
        linha.addWidget(self.botao_copiar_texto)

        return linha

    # ------------------------------------------------------------------
    # Carregamento de imagem
    # ------------------------------------------------------------------

    def _caminho_cache_imagem(self) -> Path:
        return config.CACHE_DIR / "imagens" / f"{self.produto.item_id}.imgcache"

    def _iniciar_carregamento_imagem(self) -> None:
        tarefa = _TarefaCarregarImagem(
            self.produto.imagem_url, self._caminho_cache_imagem()
        )
        tarefa.sinais.concluido.connect(self._ao_imagem_carregada)
        tarefa.sinais.falhou.connect(self._ao_imagem_falhar)
        _POOL_IMAGENS.start(tarefa)

    def _ao_imagem_carregada(self, dados: bytes) -> None:
        self._imagem_bytes = dados

        pixmap = QPixmap()
        if pixmap.loadFromData(dados):
            pixmap = pixmap.scaled(
                self.label_imagem.width(),
                self.label_imagem.height(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.label_imagem.setPixmap(pixmap)
            self.label_imagem.setText("")
        else:
            self.label_imagem.setText("Imagem inválida")

        if self._acao_pendente_apos_imagem == "copiar":
            self._acao_pendente_apos_imagem = None
            self._executar_copia_imagem(dados)

    def _ao_imagem_falhar(self, mensagem: str) -> None:
        self.label_imagem.setText("Sem imagem")
        if self._acao_pendente_apos_imagem == "copiar":
            self._acao_pendente_apos_imagem = None
            self._concluir_feedback_botao(
                self.botao_copiar_imagem, "Falhou", "📷  Imagem"
            )

    # ------------------------------------------------------------------
    # Edição manual -> recálculo de score (só deste card)
    # ------------------------------------------------------------------

    def _ao_editar_utilidade(self, valor: float) -> None:
        self.produto.utilidade_manual = valor
        self._recalcular_score()

    def _ao_editar_facilidade(self, valor: float) -> None:
        self.produto.facilidade_conteudo_manual = valor
        self._recalcular_score()

    def _recalcular_score(self) -> None:
        novo_score = calcular_score_final(self.produto, **self.fontes_auxiliares)
        self.produto.score_final = novo_score
        self._atualizar_badge_score()

        if self.on_score_changed is not None:
            self.on_score_changed(self.produto)

    # ------------------------------------------------------------------
    # Botões de cópia
    # ------------------------------------------------------------------

    def _ao_clicar_copiar_imagem(self) -> None:
        if self._imagem_bytes is not None:
            self._executar_copia_imagem(self._imagem_bytes)
            return

        # Imagem ainda não carregou (ou falhou antes) — tenta carregar
        # sob demanda e copia assim que estiver pronta.
        self.botao_copiar_imagem.setEnabled(False)
        self.botao_copiar_imagem.setText("Baixando...")
        self._acao_pendente_apos_imagem = "copiar"
        self._iniciar_carregamento_imagem()

    def _executar_copia_imagem(self, dados: bytes) -> None:
        sucesso = copiar_imagem_para_clipboard(dados)
        self._concluir_feedback_botao(
            self.botao_copiar_imagem,
            "Copiado!" if sucesso else "Falhou",
            "📷  Imagem",
        )

    def _ao_clicar_copiar_texto(self) -> None:
        template = escolher_template(self.templates_legenda)
        legenda = gerar_legenda(self.produto, template)
        sucesso = copiar_texto_para_clipboard(legenda)
        self._concluir_feedback_botao(
            self.botao_copiar_texto,
            "Copiado!" if sucesso else "Falhou",
            "📋  Texto",
        )

    def _concluir_feedback_botao(
        self, botao: QPushButton, texto_temporario: str, texto_original: str
    ) -> None:
        if not isValid(botao):
            return
        botao.setEnabled(True)
        botao.setText(texto_temporario)

        def _restaurar():
            if isValid(botao):
                botao.setText(texto_original)

        QTimer.singleShot(DURACAO_FEEDBACK_MS, _restaurar)
