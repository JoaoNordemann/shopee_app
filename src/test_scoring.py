"""
test_scoring.py

Testes unitários para scoring.py. Rode com:
    pytest src/test_scoring.py -v

Foco: valores exatamente NA fronteira de cada faixa (ex: rating 4.8 deve
dar 10, 4.79 deve dar 8) e o comportamento de fallback dos critérios
manuais (Utilidade, Facilidade de conteúdo, Reputação, Risco).
"""

import pytest

from scoring import (
    ProdutoOferta,
    calcular_score_final,
    score_avaliacao,
    score_comissao,
    score_desconto,
    score_facilidade_conteudo,
    score_reputacao_loja,
    score_risco_invertido,
    score_utilidade,
    score_volume_avaliacoes,
)


# ---------------------------------------------------------------------------
# score_avaliacao
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rating,esperado",
    [
        (2.99, 0.0),
        (3.09, 0.0),  # zona ambígua [3.0, 3.1) tratada como 0 (ver docstring)
        (3.1, 2.0),
        (3.5, 2.0),
        (3.59, 2.0),
        (3.6, 4.0),
        (3.9, 4.0),
        (3.99, 4.0),
        (4.0, 6.0),
        (4.3, 6.0),
        (4.39, 6.0),
        (4.4, 8.0),
        (4.7, 8.0),
        (4.79, 8.0),
        (4.8, 10.0),
        (5.0, 10.0),
    ],
)
def test_score_avaliacao(rating, esperado):
    assert score_avaliacao(rating) == esperado


# ---------------------------------------------------------------------------
# score_volume_avaliacoes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sales,esperado",
    [
        (0, 0.0),
        (1, 2.0),
        (10, 2.0),
        (11, 4.0),
        (50, 4.0),
        (51, 6.0),
        (200, 6.0),
        (201, 8.0),
        (500, 8.0),
        (501, 10.0),
    ],
)
def test_score_volume_avaliacoes(sales, esperado):
    assert score_volume_avaliacoes(sales) == esperado


# ---------------------------------------------------------------------------
# score_desconto
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "desconto_pct,esperado",
    [
        (0, 0.0),
        (5, 0.0),
        (6, 2.0),
        (10, 2.0),
        (11, 4.0),
        (20, 4.0),
        (21, 6.0),
        (35, 6.0),
        (36, 8.0),
        (50, 8.0),
        (51, 10.0),
    ],
)
def test_score_desconto(desconto_pct, esperado):
    assert score_desconto(desconto_pct) == esperado


# ---------------------------------------------------------------------------
# score_comissao
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "comissao_pct,esperado",
    [
        (0, 0.0),
        (2, 0.0),
        (3, 2.0),
        (5, 2.0),
        (6, 4.0),
        (10, 4.0),
        (11, 6.0),
        (20, 6.0),
        (21, 8.0),
        (40, 8.0),
        (41, 10.0),
    ],
)
def test_score_comissao(comissao_pct, esperado):
    assert score_comissao(comissao_pct) == esperado


# ---------------------------------------------------------------------------
# score_reputacao_loja / score_risco_invertido (override manual por shop_id)
# ---------------------------------------------------------------------------


def test_score_reputacao_loja_sem_override_retorna_neutro():
    assert score_reputacao_loja(shop_id=123, lista_lojas_confiaveis={}) == 5.0


def test_score_reputacao_loja_com_override():
    lista = {123: 9.0}
    assert score_reputacao_loja(shop_id=123, lista_lojas_confiaveis=lista) == 9.0


def test_score_risco_invertido_sem_override_retorna_neutro():
    assert score_risco_invertido(shop_id=123, lista_garantias={}) == 5.0


def test_score_risco_invertido_com_override():
    lista = {123: 8.0}
    assert score_risco_invertido(shop_id=123, lista_garantias=lista) == 8.0


# ---------------------------------------------------------------------------
# score_utilidade / score_facilidade_conteudo (manual -> tabela -> neutro)
# ---------------------------------------------------------------------------


def test_score_utilidade_usa_valor_manual_quando_definido():
    resultado = score_utilidade(
        categoria_ids=[100],
        utilidade_manual=7.5,
        tabela_utilidade_por_categoria={100: 2.0},
    )
    assert resultado == 7.5  # manual tem prioridade sobre a tabela


def test_score_utilidade_usa_tabela_quando_manual_ausente():
    resultado = score_utilidade(
        categoria_ids=[100, 200],
        utilidade_manual=None,
        tabela_utilidade_por_categoria={200: 6.0},
    )
    assert resultado == 6.0


def test_score_utilidade_neutro_quando_sem_manual_e_sem_tabela():
    resultado = score_utilidade(
        categoria_ids=[999],
        utilidade_manual=None,
        tabela_utilidade_por_categoria={},
    )
    assert resultado == 5.0


def test_score_facilidade_conteudo_usa_valor_manual_quando_definido():
    resultado = score_facilidade_conteudo(
        categoria_ids=[100],
        facilidade_manual=3.0,
        tabela_facilidade_por_categoria={100: 9.0},
    )
    assert resultado == 3.0


def test_score_facilidade_conteudo_neutro_quando_sem_dado():
    resultado = score_facilidade_conteudo(
        categoria_ids=[999],
        facilidade_manual=None,
        tabela_facilidade_por_categoria={},
    )
    assert resultado == 5.0


# ---------------------------------------------------------------------------
# calcular_score_final
# ---------------------------------------------------------------------------


def _produto_base(**overrides) -> ProdutoOferta:
    dados = dict(
        item_id=1,
        nome="Produto Teste",
        imagem_url="http://exemplo.com/img.jpg",
        preco=100.0,
        desconto_pct=0,
        comissao_pct=0,
        sales=0,
        rating=0.0,
        shop_id=1,
        categoria_ids=[],
    )
    dados.update(overrides)
    return ProdutoOferta(**dados)


def test_calcular_score_final_pior_caso_da_score_zero():
    # Todos os critérios objetivos no pior patamar, e critérios subjetivos
    # forçados a 0 via override, para garantir que o score final seja 0.0.
    produto = _produto_base(
        rating=0.0,
        sales=0,
        desconto_pct=0,
        comissao_pct=0,
        shop_id=1,
        utilidade_manual=0.0,
        facilidade_conteudo_manual=0.0,
    )
    score = calcular_score_final(
        produto,
        lista_lojas_confiaveis={1: 0.0},
        lista_garantias={1: 0.0},
    )
    assert score == 0.0


def test_calcular_score_final_melhor_caso_da_score_dez():
    produto = _produto_base(
        rating=5.0,
        sales=1000,
        desconto_pct=60,
        comissao_pct=50,
        shop_id=1,
        utilidade_manual=10.0,
        facilidade_conteudo_manual=10.0,
    )
    score = calcular_score_final(
        produto,
        lista_lojas_confiaveis={1: 10.0},
        lista_garantias={1: 10.0},
    )
    assert score == 10.0


def test_calcular_score_final_sem_fontes_auxiliares_usa_neutro_para_subjetivos():
    # Sem nenhuma fonte auxiliar informada e sem valores manuais, os
    # critérios subjetivos (reputação, utilidade, facilidade, risco) devem
    # cair no valor neutro (5), e o score final deve refletir isso.
    produto = _produto_base(
        rating=5.0,  # 10
        sales=1000,  # 10
        desconto_pct=60,  # 10
        comissao_pct=50,  # 10
    )
    score = calcular_score_final(produto)

    # soma ponderada = (10*15 + 10*15 + 10*5 + 5*5 + 5*35 + 5*7 + 10*15 + 5*3) / 100
    esperado = round(
        (10 * 15 + 10 * 15 + 10 * 5 + 5 * 5 + 5 * 35 + 5 * 7 + 10 * 15 + 5 * 3) / 100,
        1,
    )
    assert score == esperado


def test_calcular_score_final_arredonda_para_uma_casa_decimal():
    produto = _produto_base(
        rating=4.5,  # 8
        sales=100,  # 4
        desconto_pct=25,  # 6
        comissao_pct=15,  # 6
        utilidade_manual=7.0,
        facilidade_conteudo_manual=6.0,
    )
    score = calcular_score_final(
        produto,
        lista_lojas_confiaveis={1: 5.0},
        lista_garantias={1: 5.0},
    )
    # Resultado deve ter no máximo 1 casa decimal
    assert round(score, 1) == score
