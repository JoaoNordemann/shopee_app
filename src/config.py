"""
config.py

Carrega as variáveis de ambiente do projeto (.env), valida que as
credenciais obrigatórias existem e expõe constantes usadas pelo restante
da aplicação.

NUNCA coloque App ID/Secret diretamente no código. Eles devem vir sempre
do arquivo ".env" (veja ".env.example" para o modelo).
"""

from pathlib import Path

from dotenv import load_dotenv
import os

# ---------------------------------------------------------------------------
# Caminhos base do projeto
# ---------------------------------------------------------------------------

# Raiz do projeto (uma pasta acima de src/)
BASE_DIR = Path(__file__).resolve().parent.parent

# Pasta usada para cache local em JSON (respostas da API, ajustes manuais
# de Utilidade/Facilidade de conteúdo por produto, etc.)
CACHE_DIR = BASE_DIR / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Carrega o .env (procura automaticamente na raiz do projeto)
# ---------------------------------------------------------------------------

load_dotenv(dotenv_path=BASE_DIR / ".env")

SHOPEE_APP_ID = os.getenv("SHOPEE_APP_ID")
SHOPEE_APP_SECRET = os.getenv("SHOPEE_APP_SECRET")
SHOPEE_API_URL = os.getenv(
    "SHOPEE_API_URL", "https://open-api.affiliate.shopee.com.br/graphql"
)


def _validar_variaveis_obrigatorias() -> None:
    """
    Garante que as credenciais da Shopee Open API estão presentes no
    ambiente. Levanta um erro claro e específico se alguma faltar, em vez
    de deixar o programa falhar mais adiante com um erro genérico de
    autenticação.

    Observação: enquanto você ainda não tiver App ID/Secret aprovados,
    esta validação vai impedir a inicialização do módulo de API. Isso é
    intencional para não deixarmos credenciais ausentes passarem
    despercebidas — mas significa que, por enquanto, este config.py só
    deve ser importado por partes do app que realmente precisam da API.
    Módulos que só mexem com UI/mock não devem depender deste import.
    """
    faltando = []

    if not SHOPEE_APP_ID:
        faltando.append("SHOPEE_APP_ID")
    if not SHOPEE_APP_SECRET:
        faltando.append("SHOPEE_APP_SECRET")

    if faltando:
        raise RuntimeError(
            "Variáveis de ambiente obrigatórias ausentes: "
            f"{', '.join(faltando)}. "
            "Copie o arquivo .env.example para .env e preencha os valores "
            "antes de usar a integração com a Shopee Open API."
        )


_validar_variaveis_obrigatorias()

# ---------------------------------------------------------------------------
# Constantes gerais da aplicação
# ---------------------------------------------------------------------------

# Timeout (em segundos) para as chamadas HTTP à API da Shopee
TIMEOUT_API = 15

# Templates do texto de divulgação usado no botão "Copiar texto". Uma
# lista, não um texto único — o app sorteia um aleatoriamente a cada
# clique (ver whatsapp_export.escolher_template), pra não repetir sempre
# a mesma mensagem no canal/grupo de WhatsApp.
#
# Placeholders disponíveis em todos: {nome_produto}, {preco}, {desconto},
# {link}. Edite, remova ou adicione quantos quiser — a lista pode ter
# qualquer tamanho.
TEMPLATES_TEXTO_WHATSAPP = [
    # 1) Urgência clássica
    (
        "🔥 OFERTA IMPERDÍVEL! 🔥\n\n"
        "{nome_produto}\n\n"
        "💰 De olho nesse preço: R$ {preco}\n"
        "📉 {desconto}% de desconto\n\n"
        "🛒 Compre aqui: {link}"
    ),
    # 2) Direto ao ponto, sem enrolação
    (
        "{nome_produto}\n"
        "R$ {preco} ({desconto}% OFF)\n\n"
        "Link: {link}"
    ),
    # 3) Pergunta pra engajar antes de vender
    (
        "Já estava de olho em algo assim? 👀\n\n"
        "{nome_produto} com {desconto}% de desconto — "
        "saiu por R$ {preco}.\n\n"
        "Corre que é por tempo limitado: {link}"
    ),
    # 4) Escassez / senso de oportunidade
    (
        "⚡ Achado do dia ⚡\n\n"
        "{nome_produto}\n"
        "De olho nesse preço: R$ {preco} (eram {desconto}% mais caro!)\n\n"
        "Não sei até quando esse preço vai durar 👉 {link}"
    ),
    # 5) Tom mais pessoal/storytelling
    (
        "Gente, achei isso aqui e não resisti 😍\n\n"
        "{nome_produto}\n"
        "Tá com {desconto}% de desconto, saiu por R$ {preco}.\n\n"
        "Deixo o link pra quem quiser garantir também: {link}"
    ),
    # 6) Foco em benefício/practicidade, tom mais neutro
    (
        "Recomendação de hoje: {nome_produto}\n\n"
        "✅ {desconto}% de desconto\n"
        "✅ R$ {preco}\n\n"
        "Confira: {link}"
    ),
    # 7) Humor leve / descontraído
    (
        "Meu cartão de crédito não vai gostar disso, mas... 😅\n\n"
        "{nome_produto} — R$ {preco} com {desconto}% OFF\n\n"
        "{link}"
    ),
    # 8) Chamada curta pra grupos com pouca paciência para textão
    (
        "📦 {nome_produto}\n"
        "💸 R$ {preco} | {desconto}% OFF\n"
        "🔗 {link}"
    ),
]
