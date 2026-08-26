"""
data_service.py

Camada de serviço entre a API da Shopee (shopee_api.py) e a UI. É este
módulo que a interface vai importar e chamar — a UI não deve falar
diretamente com ShopeeAffiliateClient nem com scoring.py.

Responsável por:
    - Buscar ofertas em algumas combinações de list_type/sort_type que
      façam sentido para "ofertas mais atrativas", combinando e
      deduplicando os resultados.
    - Normalizar cada item bruto da API em um ProdutoOferta.
    - Calcular o score_final de cada produto (scoring.py).
    - Ordenar por score decrescente.
    - Persistir/ler um cache local em JSON, para a UI abrir rápido com
      dados existentes sem esperar a API.
    - Traduzir qualquer falha (rede, API, cache corrompido) em uma
      exceção amigável (OfertaServiceError) para a UI mostrar ao usuário,
      sem deixar stack traces "crus" estourarem na interface.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path

import config
from scoring import ProdutoOferta, calcular_score_final
from shopee_api import ShopeeAffiliateClient, ShopeeApiError

logger = logging.getLogger(__name__)


class OfertaServiceError(Exception):
    """Erro amigável para a UI, cobrindo qualquer falha deste serviço."""


# ---------------------------------------------------------------------------
# Configuração de busca
# ---------------------------------------------------------------------------

# Combinações de (list_type, sort_type) usadas para montar o conjunto de
# "ofertas mais atrativas".
#
# Valores confirmados contra a documentação da Shopee Affiliate API
# (productOfferV2) em busca dedicada — fonte não-oficial mas detalhada
# (affiliateshopee.com.br/documentacao), então trate como "melhor
# evidência disponível" e valide empiricamente com sua conta se notar
# comportamento diferente:
#   listType: 0=Recomendados, 1=Maior comissão, 2=Top performance
#             (são POOLS DE PRODUTOS DIFERENTES, não a mesma lista
#             reordenada — por isso buscamos nos 3, não só no 0)
#   sortType: 1=Relevância, 2=Vendidos, 3=Maior preço, 4=Menor preço,
#             5=Comissão
#
# Isso corrige uma inversão que tínhamos antes (achávamos que sortType=2
# era comissão e 5 era vendas — é o contrário) e amplia a cobertura para
# os 3 listType em vez de só o 0, o que sozinho já deixava de fora 2/3
# do universo de produtos disponíveis na API.
COMBINACOES_BUSCA: list[tuple[int, int]] = [
    (0, 5),  # Recomendados, ordenado por comissão
    (1, 5),  # Maior comissão, ordenado por comissão
    (2, 5),  # Top performance, ordenado por comissão
    (0, 2),  # Recomendados, ordenado por vendidos (sinal de popularidade)
]

PAGINAS_POR_COMBINACAO = 200  # teto de segurança bem alto; prioriza volume máximo (decisão do usuário — pode levar minutos por atualização)
ITENS_POR_PAGINA = 50  # limite REAL confirmado pela API (erro 11001 ao tentar mais que isso)

NOME_ARQUIVO_CACHE = "cache_ofertas.json"
IDADE_MAXIMA_CACHE_MINUTOS = 30

# Arquivo opcional, editado manualmente, com o mapeamento categoria_id ->
# nome legível. A Shopee Affiliate API (productOfferV2) não devolve nome
# de categoria — só os IDs numéricos em productCatIds — e não existe,
# nesta API, nenhum endpoint tipo "listar categorias com nome" (isso só
# existe na API de Seller/Partner, que usa OAuth por loja e não é
# compatível com as credenciais de afiliado usadas aqui). Por isso o
# mapeamento é local e mantido à mão: crie/edite
# cache/categorias_nomes.json no formato:
#     {"100629": "Eletrônicos", "100636": "Beleza"}
# Categorias sem nome mapeado continuam funcionando normalmente no
# filtro — a UI só mostra "Categoria {id}" pra elas.
NOME_ARQUIVO_CATEGORIAS = "categorias_nomes.json"


class OfertaService:
    """Serviço de alto nível consumido pela UI."""

    def __init__(
        self,
        client: ShopeeAffiliateClient,
        cache_dir: Path | None = None,
        lista_lojas_confiaveis: dict | None = None,
        lista_garantias: dict | None = None,
        tabela_utilidade_por_categoria: dict | None = None,
        tabela_facilidade_por_categoria: dict | None = None,
        nomes_categorias: dict | None = None,
    ):
        """
        client: instância já configurada de ShopeeAffiliateClient.
        cache_dir: pasta onde o cache JSON é salvo (default: config.CACHE_DIR).
        As demais fontes auxiliares são opcionais e alimentam o cálculo de
        score (ver scoring.py). Enquanto você não tiver esses JSONs
        prontos (lojas confiáveis, garantias, tabelas por categoria),
        basta não passar nada — os critérios correspondentes usam o
        valor neutro (5) automaticamente.

        nomes_categorias: mapeamento categoria_id -> nome legível, usado
        só pela UI para mostrar nomes em vez de IDs crus no filtro de
        categoria. Se não for passado explicitamente, tentamos carregar
        automaticamente de cache/categorias_nomes.json (ver
        NOME_ARQUIVO_CATEGORIAS); se o arquivo não existir, fica {} e a
        UI mostra "Categoria {id}" normalmente.
        """
        self.client = client
        self.cache_dir = cache_dir or config.CACHE_DIR
        self.lista_lojas_confiaveis = lista_lojas_confiaveis or {}
        self.lista_garantias = lista_garantias or {}
        self.tabela_utilidade_por_categoria = tabela_utilidade_por_categoria or {}
        self.tabela_facilidade_por_categoria = tabela_facilidade_por_categoria or {}
        self.nomes_categorias = (
            nomes_categorias
            if nomes_categorias is not None
            else self._carregar_nomes_categorias_do_arquivo()
        )

    def _carregar_nomes_categorias_do_arquivo(self) -> dict[int, str]:
        """
        Lê cache/categorias_nomes.json (ver NOME_ARQUIVO_CATEGORIAS), se
        existir. Arquivo ausente ou corrompido não é erro fatal — só
        significa "sem nomes mapeados ainda", e a UI cai no fallback de
        mostrar o ID puro.
        """
        caminho = self.cache_dir / NOME_ARQUIVO_CATEGORIAS
        if not caminho.exists():
            return {}

        try:
            with open(caminho, "r", encoding="utf-8") as f:
                bruto = json.load(f)
        except (OSError, ValueError) as exc:
            logger.warning(
                "Não foi possível carregar nomes de categoria de %s: %s",
                caminho,
                exc,
            )
            return {}

        nomes: dict[int, str] = {}
        for chave, valor in bruto.items():
            try:
                nomes[int(chave)] = str(valor)
            except (ValueError, TypeError):
                # Chaves não numéricas (ex: um campo "_comentario" no
                # arquivo de exemplo) são ignoradas em vez de derrubar o
                # carregamento do restante do mapeamento.
                continue
        return nomes

    # ------------------------------------------------------------------
    # Busca + normalização + score
    # ------------------------------------------------------------------

    def atualizar_ofertas(self) -> list[ProdutoOferta]:
        """
        Busca ofertas na API (várias combinações), normaliza, calcula o
        score de cada produto, ordena por score decrescente, salva no
        cache local e retorna a lista pronta para a UI exibir.

        Levanta OfertaServiceError (mensagem amigável) em caso de falha —
        nunca deixa ShopeeApiError ou outra exceção "crua" escapar.
        """
        try:
            itens_brutos = self._buscar_itens_combinados()
        except ShopeeApiError as exc:
            logger.error("Falha ao atualizar ofertas: %s", exc)
            raise OfertaServiceError(
                "Não foi possível buscar as ofertas na Shopee agora. "
                f"Detalhe técnico: {exc}"
            ) from exc
        except Exception as exc:  # segurança extra: nunca deixar vazar "cru"
            logger.exception("Erro inesperado ao atualizar ofertas.")
            raise OfertaServiceError(
                "Ocorreu um erro inesperado ao atualizar as ofertas. "
                "Veja o log para mais detalhes."
            ) from exc

        if not itens_brutos:
            logger.warning("A busca não retornou nenhuma oferta.")
            raise OfertaServiceError(
                "A Shopee não retornou nenhuma oferta para os critérios "
                "de busca atuais."
            )

        # Preserva ajustes manuais (Utilidade/Facilidade de conteúdo) já
        # feitos anteriormente na UI, para o botão "Atualizar" não apagar
        # esse trabalho manual a cada refresh.
        manuais_anteriores = self._carregar_valores_manuais_do_cache()

        produtos: list[ProdutoOferta] = []
        for item in itens_brutos:
            try:
                produto = self._mapear_item_para_produto(item, manuais_anteriores)
            except (KeyError, TypeError, ValueError) as exc:
                logger.warning(
                    "Item ignorado por formato inesperado: %s (%s)", item, exc
                )
                continue

            score = calcular_score_final(
                produto,
                lista_lojas_confiaveis=self.lista_lojas_confiaveis,
                lista_garantias=self.lista_garantias,
                tabela_utilidade_por_categoria=self.tabela_utilidade_por_categoria,
                tabela_facilidade_por_categoria=self.tabela_facilidade_por_categoria,
            )
            produto.score_final = score
            produtos.append(produto)

        produtos.sort(key=lambda p: p.score_final or 0.0, reverse=True)

        self._salvar_cache(produtos)

        logger.info("Atualização concluída: %d produtos processados.", len(produtos))
        return produtos

    def _buscar_itens_combinados(self) -> list[dict]:
        """
        Busca ofertas em todas as COMBINACOES_BUSCA e devolve uma lista
        única, deduplicada por itemId (mantém a primeira ocorrência).
        """
        itens_por_id: dict[int, dict] = {}

        for list_type, sort_type in COMBINACOES_BUSCA:
            itens = self.client.buscar_varias_paginas(
                list_type=list_type,
                sort_type=sort_type,
                max_paginas=PAGINAS_POR_COMBINACAO,
                limit=ITENS_POR_PAGINA,
            )
            for item in itens:
                item_id = item.get("itemId")
                if item_id is None:
                    continue
                if item_id not in itens_por_id:
                    itens_por_id[item_id] = item

        return list(itens_por_id.values())

    @staticmethod
    def _percentual(valor) -> float:
        """
        Normaliza campos percentuais da API para a escala 0-100 usada
        pelas funções de score.

        SUPOSIÇÃO A VALIDAR: campos como priceDiscountRate e
        commissionRate podem vir como fração (ex: 0.15 = 15%) ou já como
        percentual (ex: 15). Como isso não foi confirmado na
        documentação disponível, tratamos valores entre 0 e 1 (inclusive)
        como fração e multiplicamos por 100; valores maiores são
        assumidos como já sendo percentual. Ajuste esta função assim que
        você confirmar o formato real retornado pela API.
        """
        if valor is None:
            return 0.0
        valor = float(valor)
        if 0 <= valor <= 1:
            return valor * 100
        return valor

    def _mapear_item_para_produto(
        self, item: dict, manuais_anteriores: dict
    ) -> ProdutoOferta:
        """Converte um dicionário bruto da API em um ProdutoOferta."""
        item_id = int(item["itemId"])
        manual = manuais_anteriores.get(item_id, {})

        return ProdutoOferta(
            item_id=item_id,
            nome=item.get("productName", "(sem nome)"),
            imagem_url=item.get("imageUrl", ""),
            preco=float(item.get("priceMin", 0) or 0),
            desconto_pct=self._percentual(item.get("priceDiscountRate")),
            comissao_pct=self._percentual(item.get("commissionRate")),
            sales=int(item.get("sales", 0) or 0),
            rating=float(item.get("ratingStar", 0) or 0),
            shop_id=int(item.get("shopId", 0) or 0),
            categoria_ids=list(item.get("productCatIds") or []),
            link_produto=item.get("productLink", ""),
            link_oferta=item.get("offerLink", ""),
            utilidade_manual=manual.get("utilidade_manual"),
            facilidade_conteudo_manual=manual.get("facilidade_conteudo_manual"),
        )

    # ------------------------------------------------------------------
    # Cache local
    # ------------------------------------------------------------------

    def _caminho_cache(self) -> Path:
        return self.cache_dir / NOME_ARQUIVO_CACHE

    def _salvar_cache(self, produtos: list[ProdutoOferta]) -> None:
        """Salva os produtos no cache JSON local, com timestamp da atualização."""
        conteudo = {
            "atualizado_em": datetime.now().isoformat(),
            "produtos": [asdict(p) for p in produtos],
        }
        caminho = self._caminho_cache()
        try:
            caminho.parent.mkdir(parents=True, exist_ok=True)
            with open(caminho, "w", encoding="utf-8") as f:
                json.dump(conteudo, f, ensure_ascii=False, indent=2)
            logger.info("Cache salvo em %s (%d produtos).", caminho, len(produtos))
        except OSError as exc:
            # Falha ao salvar cache não deve impedir a UI de exibir os
            # dados que acabaram de ser buscados — só logamos o problema.
            logger.error("Não foi possível salvar o cache em %s: %s", caminho, exc)

    def carregar_do_cache(
        self, idade_maxima_minutos: int = IDADE_MAXIMA_CACHE_MINUTOS
    ) -> list[ProdutoOferta] | None:
        """
        Lê o cache local, se existir e não estiver mais velho que
        `idade_maxima_minutos`. Retorna None se o cache não existe, está
        expirado, ou está corrompido — nesses casos a UI deve chamar
        atualizar_ofertas() para buscar dados frescos.
        """
        caminho = self._caminho_cache()

        if not caminho.exists():
            logger.info("Nenhum cache encontrado em %s.", caminho)
            return None

        try:
            with open(caminho, "r", encoding="utf-8") as f:
                conteudo = json.load(f)

            atualizado_em = datetime.fromisoformat(conteudo["atualizado_em"])
            idade = datetime.now() - atualizado_em

            if idade > timedelta(minutes=idade_maxima_minutos):
                logger.info(
                    "Cache encontrado, mas expirado (idade: %s).", idade
                )
                return None

            produtos = [ProdutoOferta(**dados) for dados in conteudo["produtos"]]
            logger.info(
                "Cache carregado de %s: %d produtos (idade: %s).",
                caminho,
                len(produtos),
                idade,
            )
            return produtos

        except (OSError, ValueError, KeyError, TypeError) as exc:
            # Cache corrompido, formato antigo incompatível, JSON inválido
            # etc. Não é um erro fatal: apenas tratamos como "sem cache".
            logger.warning("Cache em %s inválido ou corrompido: %s", caminho, exc)
            return None

    def _carregar_valores_manuais_do_cache(self) -> dict[int, dict]:
        """
        Lê o cache atual (se existir, mesmo que expirado) só para
        recuperar os valores manuais de utilidade/facilidade de conteúdo
        já ajustados pelo usuário, e preservá-los na próxima atualização.
        """
        caminho = self._caminho_cache()
        if not caminho.exists():
            return {}

        try:
            with open(caminho, "r", encoding="utf-8") as f:
                conteudo = json.load(f)
            return {
                p["item_id"]: {
                    "utilidade_manual": p.get("utilidade_manual"),
                    "facilidade_conteudo_manual": p.get("facilidade_conteudo_manual"),
                }
                for p in conteudo.get("produtos", [])
            }
        except (OSError, ValueError, KeyError, TypeError) as exc:
            logger.warning(
                "Não foi possível recuperar valores manuais do cache anterior: %s",
                exc,
            )
            return {}
