"""
scoring.py

Módulo independente da API e da UI. Recebe dados já normalizados de um
produto e calcula o Score de rentabilidade (0 a 10) conforme o modelo de
pesos definido para o projeto.

Pesos (somam 100):
    Avaliação do produto ............. 15
    Volume de avaliações (sales) ..... 15
    Desconto .......................... 5
    Reputação da loja ................. 5
    Utilidade ......................... 35
    Facilidade de conteúdo ............. 7
    Comissão .......................... 15
    Risco invertido (garantia) ......... 3

Observação sobre "Utilidade" e "Facilidade de conteúdo": por decisão do
projeto (opção "a"), ambos são inputs MANUAIS, editáveis por produto na UI
(não vêm da API nem de heurística automática). Enquanto o valor manual não
foi definido, usamos um valor neutro (5) como placeholder — ou, se uma
tabela de utilidade/facilidade "típica por categoria" for fornecida, usamos
essa tabela como fallback antes do neutro puro. Isso evita que um produto
ainda não avaliado manualmente derrube o score para 0 de forma enganosa.

Nota: o pedido original não incluía um campo específico para
"facilidade_conteudo" na dataclass nem uma função de score dedicada, mas
como esse critério tem peso de 7% no score final, adicionei
`facilidade_conteudo_manual` ao ProdutoOferta e a função
`score_facilidade_conteudo`, espelhando exatamente o tratamento dado à
Utilidade (mesma decisão "opção a" já confirmada para os dois critérios).
Sinalizando aqui para deixar a mudança explícita.
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Modelo de dados
# ---------------------------------------------------------------------------


@dataclass
class ProdutoOferta:
    """Representa um produto já normalizado, pronto para ter o score calculado."""

    item_id: int
    nome: str
    imagem_url: str
    preco: float
    desconto_pct: float
    comissao_pct: float
    sales: int
    rating: float
    shop_id: int
    categoria_ids: list[int] = field(default_factory=list)
    link_produto: str = ""
    link_oferta: str = ""

    # Inputs manuais/subjetivos (opção "a": editáveis na UI). None = ainda
    # não avaliado manualmente pelo usuário.
    utilidade_manual: float | None = None
    facilidade_conteudo_manual: float | None = None

    # Preenchido por calcular_score_final() (via data_service.py). None
    # até o primeiro cálculo. Guardado na própria instância para a UI
    # poder exibir e ordenar sem precisar recalcular a cada acesso.
    score_final: float | None = None


# ---------------------------------------------------------------------------
# Pesos do modelo (somam 100)
# ---------------------------------------------------------------------------

PESO_AVALIACAO = 15
PESO_VOLUME_AVALIACOES = 15
PESO_DESCONTO = 5
PESO_REPUTACAO_LOJA = 5
PESO_UTILIDADE = 35
PESO_FACILIDADE_CONTEUDO = 7
PESO_COMISSAO = 15
PESO_RISCO_INVERTIDO = 3

SOMA_PESOS = (
    PESO_AVALIACAO
    + PESO_VOLUME_AVALIACOES
    + PESO_DESCONTO
    + PESO_REPUTACAO_LOJA
    + PESO_UTILIDADE
    + PESO_FACILIDADE_CONTEUDO
    + PESO_COMISSAO
    + PESO_RISCO_INVERTIDO
)  # == 100

# Valor neutro usado quando não há dado disponível para um critério
# subjetivo/estimado (reputação, risco, utilidade e facilidade antes do
# ajuste manual/tabela).
SCORE_NEUTRO = 5.0


# ---------------------------------------------------------------------------
# Funções de score por critério (puras: mesmo input -> mesmo output)
# ---------------------------------------------------------------------------


def score_avaliacao(rating: float) -> float:
    """
    Nota do produto (ratingStar), faixas:
    <3.0 -> 0 | 3.1-3.5 -> 2 | 3.6-3.9 -> 4 | 4.0-4.3 -> 6
    4.4-4.7 -> 8 | 4.8-5.0 -> 10

    Observação de borda: o intervalo [3.0, 3.1) não foi especificado nas
    faixas originais; por segurança, tratamos qualquer valor < 3.1 como 0.
    """
    if rating >= 4.8:
        return 10.0
    if rating >= 4.4:
        return 8.0
    if rating >= 4.0:
        return 6.0
    if rating >= 3.6:
        return 4.0
    if rating >= 3.1:
        return 2.0
    return 0.0


def score_volume_avaliacoes(sales: int) -> float:
    """
    Proxy de volume de avaliações = quantidade vendida (sales).
    0 -> 0 | 1-10 -> 2 | 11-50 -> 4 | 51-200 -> 6 | 201-500 -> 8 | >500 -> 10
    """
    if sales > 500:
        return 10.0
    if sales >= 201:
        return 8.0
    if sales >= 51:
        return 6.0
    if sales >= 11:
        return 4.0
    if sales >= 1:
        return 2.0
    return 0.0


def score_desconto(desconto_pct: float) -> float:
    """
    Desconto (priceDiscountRate), faixas:
    0-5% -> 0 | 6-10% -> 2 | 11-20% -> 4 | 21-35% -> 6 | 36-50% -> 8 | >50% -> 10
    """
    if desconto_pct > 50:
        return 10.0
    if desconto_pct >= 36:
        return 8.0
    if desconto_pct >= 21:
        return 6.0
    if desconto_pct >= 11:
        return 4.0
    if desconto_pct >= 6:
        return 2.0
    return 0.0


def score_reputacao_loja(shop_id: int, lista_lojas_confiaveis: dict) -> float:
    """
    Reputação da loja: sem dado nativo da API. Usa override manual por
    shop_id (dict {shop_id: score_0_a_10}) se existir; caso contrário,
    retorna o valor neutro (5).

    `lista_lojas_confiaveis` é o JSON mantido manualmente pelo usuário.
    """
    if shop_id in lista_lojas_confiaveis:
        return float(lista_lojas_confiaveis[shop_id])
    return SCORE_NEUTRO


def score_utilidade(
    categoria_ids: list[int],
    utilidade_manual: float | None,
    tabela_utilidade_por_categoria: dict,
) -> float:
    """
    Utilidade (subjetivo, sem dado da API). Prioridade:
    1) valor manual, se já definido pelo usuário na UI;
    2) valor da tabela de utilidade típica por categoria, se alguma das
       categorias do produto estiver mapeada;
    3) valor neutro (5), como último fallback.
    """
    if utilidade_manual is not None:
        return float(utilidade_manual)

    for cat_id in categoria_ids:
        if cat_id in tabela_utilidade_por_categoria:
            return float(tabela_utilidade_por_categoria[cat_id])

    return SCORE_NEUTRO


def score_facilidade_conteudo(
    categoria_ids: list[int],
    facilidade_manual: float | None,
    tabela_facilidade_por_categoria: dict,
) -> float:
    """
    Facilidade de conteúdo (subjetivo, sem dado da API). Mesma lógica de
    prioridade de score_utilidade: manual -> tabela por categoria -> neutro.
    """
    if facilidade_manual is not None:
        return float(facilidade_manual)

    for cat_id in categoria_ids:
        if cat_id in tabela_facilidade_por_categoria:
            return float(tabela_facilidade_por_categoria[cat_id])

    return SCORE_NEUTRO


def score_comissao(comissao_pct: float) -> float:
    """
    Comissão (commissionRate), faixas:
    0-2% -> 0 | 3-5% -> 2 | 6-10% -> 4 | 11-20% -> 6 | 21-40% -> 8 | >40% -> 10
    """
    if comissao_pct > 40:
        return 10.0
    if comissao_pct >= 21:
        return 8.0
    if comissao_pct >= 11:
        return 6.0
    if comissao_pct >= 6:
        return 4.0
    if comissao_pct >= 3:
        return 2.0
    return 0.0


def score_risco_invertido(shop_id: int, lista_garantias: dict) -> float:
    """
    Risco invertido (garantia/devolução): sem dado direto da API. Mesmo
    tratamento da reputação da loja — override manual por shop_id, com
    valor neutro (5) como default.
    """
    if shop_id in lista_garantias:
        return float(lista_garantias[shop_id])
    return SCORE_NEUTRO


# ---------------------------------------------------------------------------
# Score final
# ---------------------------------------------------------------------------


def calcular_score_final(produto: ProdutoOferta, **fontes_auxiliares) -> float:
    """
    Calcula o score final (0 a 10, 1 casa decimal) de um ProdutoOferta,
    aplicando os pesos do modelo.

    fontes_auxiliares (todas opcionais, default {} quando omitidas):
        lista_lojas_confiaveis: dict[int, float]
        lista_garantias: dict[int, float]
        tabela_utilidade_por_categoria: dict[int, float]
        tabela_facilidade_por_categoria: dict[int, float]
    """
    lista_lojas_confiaveis = fontes_auxiliares.get("lista_lojas_confiaveis", {})
    lista_garantias = fontes_auxiliares.get("lista_garantias", {})
    tabela_utilidade_por_categoria = fontes_auxiliares.get(
        "tabela_utilidade_por_categoria", {}
    )
    tabela_facilidade_por_categoria = fontes_auxiliares.get(
        "tabela_facilidade_por_categoria", {}
    )

    s_avaliacao = score_avaliacao(produto.rating)
    s_volume = score_volume_avaliacoes(produto.sales)
    s_desconto = score_desconto(produto.desconto_pct)
    s_reputacao = score_reputacao_loja(produto.shop_id, lista_lojas_confiaveis)
    s_utilidade = score_utilidade(
        produto.categoria_ids, produto.utilidade_manual, tabela_utilidade_por_categoria
    )
    s_facilidade = score_facilidade_conteudo(
        produto.categoria_ids,
        produto.facilidade_conteudo_manual,
        tabela_facilidade_por_categoria,
    )
    s_comissao = score_comissao(produto.comissao_pct)
    s_risco = score_risco_invertido(produto.shop_id, lista_garantias)

    soma_ponderada = (
        s_avaliacao * PESO_AVALIACAO
        + s_volume * PESO_VOLUME_AVALIACOES
        + s_desconto * PESO_DESCONTO
        + s_reputacao * PESO_REPUTACAO_LOJA
        + s_utilidade * PESO_UTILIDADE
        + s_facilidade * PESO_FACILIDADE_CONTEUDO
        + s_comissao * PESO_COMISSAO
        + s_risco * PESO_RISCO_INVERTIDO
    )

    score_final = soma_ponderada / SOMA_PESOS
    return round(score_final, 1)
