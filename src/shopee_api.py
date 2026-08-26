"""
shopee_api.py

Cliente para a Shopee Open API - Affiliate (GraphQL).

Responsável por:
    - Montar a assinatura SHA256 exigida pela API a cada requisição.
    - Executar a query productOfferV2 (busca de ofertas de afiliado).
    - Paginar automaticamente até um limite configurável.
    - Tratar e logar erros de rede e erros retornados pelo próprio GraphQL,
      relançando-os como ShopeeApiError (mensagem amigável para a UI).

Este módulo não depende da UI nem do scoring.py — ele apenas busca e
devolve os dados brutos (já sem o envelope do GraphQL) prontos para serem
normalizados/scorados em outro lugar.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass

import requests

logger = logging.getLogger(__name__)
if not logger.handlers:
    # Configuração básica caso o módulo seja usado isoladamente (ex: via
    # `python shopee_api.py`). Quando integrado à aplicação completa, a
    # configuração de logging do app (se houver) prevalece.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


class ShopeeApiError(Exception):
    """Erro amigável para qualquer falha ao falar com a Shopee Affiliate API."""


# Código de erro "Rate Limit" documentado pela Shopee (extensions.code).
# Ao encontrá-lo, tentamos de novo com backoff exponencial em vez de
# desistir na primeira tentativa — importante agora que paginamos muito
# mais agressivamente do que antes.
CODIGO_ERRO_RATE_LIMIT = 10030
MAX_TENTATIVAS_RATE_LIMIT = 3


@dataclass
class PaginaOfertas:
    """Uma página de resultados de productOfferV2: itens + se há mais."""

    nodes: list[dict]
    has_next_page: bool


# Campos pedidos ao productOfferV2. Mantidos numa constante para facilitar
# manutenção caso a API mude ou você queira pedir mais/menos campos.
_CAMPOS_PRODUTO = """
    itemId
    productName
    imageUrl
    priceMin
    priceMax
    priceDiscountRate
    commissionRate
    commission
    sales
    ratingStar
    shopId
    productCatIds
    offerLink
    productLink
"""

_QUERY_PRODUCT_OFFER_V2 = f"""
query Offers($listType: Int, $sortType: Int, $page: Int, $limit: Int) {{
  productOfferV2(listType: $listType, sortType: $sortType, page: $page, limit: $limit) {{
    nodes {{
      {_CAMPOS_PRODUTO}
    }}
    pageInfo {{
      hasNextPage
    }}
  }}
}}
"""


class ShopeeAffiliateClient:
    """Cliente HTTP para a Shopee Open API - Affiliate (GraphQL)."""

    def __init__(self, app_id: str, app_secret: str, api_url: str, timeout: int = 15):
        self.app_id = app_id
        self.app_secret = app_secret
        self.api_url = api_url
        self.timeout = timeout

    # ------------------------------------------------------------------
    # Assinatura
    # ------------------------------------------------------------------

    def _build_signature(self, payload: str) -> tuple[str, str]:
        """
        Gera o timestamp (unix time, em segundos) e o header Authorization
        assinado, conforme o esquema:

            Signature = SHA256(app_id + timestamp + payload + secret)
            Authorization: SHA256 Credential={app_id},Timestamp={timestamp},Signature={signature}

        `payload` deve ser exatamente a string JSON que será enviada no
        corpo do POST (mesma serialização, sem alterações posteriores),
        já que a assinatura é calculada sobre esse texto exato.

        Retorna (timestamp, authorization_header).
        """
        timestamp = str(int(time.time()))
        base_string = f"{self.app_id}{timestamp}{payload}{self.app_secret}"
        signature = hashlib.sha256(base_string.encode("utf-8")).hexdigest()

        authorization_header = (
            f"SHA256 Credential={self.app_id},"
            f"Timestamp={timestamp},"
            f"Signature={signature}"
        )
        return timestamp, authorization_header

    # ------------------------------------------------------------------
    # Requisição HTTP + parsing
    # ------------------------------------------------------------------

    def _post_graphql(self, query: str, variables: dict, _tentativa: int = 1) -> dict:
        """
        Monta o payload, assina, faz o POST e devolve o corpo já
        decodificado como dict. Trata erros de rede e erros retornados
        pelo GraphQL, sempre relançando como ShopeeApiError.

        Exceção: se o erro for especificamente "Rate Limit" (código
        10030, documentado pela Shopee), tenta de novo com backoff
        exponencial (2s, 4s, 8s) até MAX_TENTATIVAS_RATE_LIMIT, em vez de
        desistir na primeira — relevante agora que paginamos de forma
        bem mais agressiva do que antes.
        """
        body = {"query": query, "variables": variables}

        # IMPORTANTE: usamos json.dumps manualmente (em vez do parâmetro
        # `json=` do requests) para termos a string EXATA que será
        # enviada e assinada — a assinatura é calculada sobre o payload
        # bruto, então payload assinado e payload enviado precisam ser
        # idênticos byte a byte.
        payload = json.dumps(body, separators=(",", ":"), ensure_ascii=False)

        timestamp, authorization_header = self._build_signature(payload)

        headers = {
            "Content-Type": "application/json",
            "Authorization": authorization_header,
        }

        try:
            response = requests.post(
                self.api_url,
                data=payload.encode("utf-8"),
                headers=headers,
                timeout=self.timeout,
            )
        except requests.exceptions.Timeout as exc:
            logger.error("Timeout ao chamar a Shopee Affiliate API: %s", exc)
            raise ShopeeApiError(
                "A Shopee demorou demais para responder. Tente novamente."
            ) from exc
        except requests.exceptions.ConnectionError as exc:
            logger.error("Erro de conexão com a Shopee Affiliate API: %s", exc)
            raise ShopeeApiError(
                "Não foi possível conectar à Shopee. Verifique sua internet."
            ) from exc
        except requests.exceptions.RequestException as exc:
            logger.error("Erro inesperado de rede ao chamar a Shopee API: %s", exc)
            raise ShopeeApiError("Erro de rede ao falar com a Shopee.") from exc

        if response.status_code != 200:
            logger.error(
                "HTTP %s ao chamar a Shopee Affiliate API. Corpo: %s",
                response.status_code,
                response.text[:500],
            )
            raise ShopeeApiError(
                f"A Shopee respondeu com erro HTTP {response.status_code}."
            )

        try:
            corpo = response.json()
        except ValueError as exc:
            logger.error("Resposta da Shopee não é um JSON válido: %s", response.text[:500])
            raise ShopeeApiError("A Shopee retornou uma resposta inválida.") from exc

        # Erros de nível GraphQL (assinatura inválida, timestamp expirado,
        # credencial sem acesso a este recurso, etc.) vêm na chave "errors",
        # mesmo com HTTP 200.
        if "errors" in corpo and corpo["errors"]:
            codigos = [
                erro.get("extensions", {}).get("code") for erro in corpo["errors"]
            ]

            if (
                CODIGO_ERRO_RATE_LIMIT in codigos
                and _tentativa <= MAX_TENTATIVAS_RATE_LIMIT
            ):
                espera_segundos = 2**_tentativa  # 2s, 4s, 8s
                logger.warning(
                    "Rate limit da Shopee (código %s). Tentativa %d/%d — "
                    "aguardando %ds antes de tentar de novo.",
                    CODIGO_ERRO_RATE_LIMIT,
                    _tentativa,
                    MAX_TENTATIVAS_RATE_LIMIT,
                    espera_segundos,
                )
                time.sleep(espera_segundos)
                return self._post_graphql(query, variables, _tentativa=_tentativa + 1)

            mensagens = "; ".join(
                erro.get("message", "Erro desconhecido") for erro in corpo["errors"]
            )
            logger.error("Erro retornado pelo GraphQL da Shopee: %s", mensagens)
            raise ShopeeApiError(f"Erro da Shopee: {mensagens}")

        return corpo

    # ------------------------------------------------------------------
    # Métodos públicos
    # ------------------------------------------------------------------

    def buscar_ofertas(
        self,
        list_type: int = 0,
        sort_type: int = 5,
        page: int = 1,
        limit: int = 50,
    ) -> PaginaOfertas:
        """
        Busca uma página de ofertas via productOfferV2.

        IMPORTANTE: `page` começa em 1, não em 0 — confirmado pelos
        exemplos da documentação (todos usam page: 1 na primeira
        chamada). Isso foi um bug real na versão anterior deste método.

        `limit`: o MÁXIMO REAL confirmado pela própria API é 50 —
        valores maiores retornam erro 11001 ("Exceeded the maximum
        number of page limit, the maximum limit is 50"). A documentação
        não-oficial mencionava até 500, mas isso valia para o endpoint
        irmão (shopeeOfferV2), não para productOfferV2 — confirmado
        empiricamente ao testar.

        Retorna um PaginaOfertas com os nodes da página (já sem o
        envelope do GraphQL) e has_next_page, para quem chama decidir se
        continua paginando.
        """
        variables = {
            "listType": list_type,
            "sortType": sort_type,
            "page": page,
            "limit": limit,
        }

        logger.info(
            "Buscando ofertas Shopee (listType=%s, sortType=%s, page=%s, limit=%s)",
            list_type,
            sort_type,
            page,
            limit,
        )

        corpo = self._post_graphql(_QUERY_PRODUCT_OFFER_V2, variables)

        try:
            offer_data = corpo["data"]["productOfferV2"]
            nodes = offer_data.get("nodes", []) or []
            has_next_page = bool(offer_data.get("pageInfo", {}).get("hasNextPage", False))
        except (KeyError, TypeError) as exc:
            logger.error("Formato inesperado na resposta da Shopee: %s", corpo)
            raise ShopeeApiError(
                "A resposta da Shopee veio num formato inesperado."
            ) from exc

        logger.info(
            "Recebidos %d itens (page=%s, hasNextPage=%s).",
            len(nodes),
            page,
            has_next_page,
        )
        return PaginaOfertas(nodes=nodes, has_next_page=has_next_page)

    def buscar_varias_paginas(
        self,
        list_type: int = 0,
        sort_type: int = 5,
        max_paginas: int = 20,
        limit: int = 50,
        pausa_entre_paginas_segundos: float = 0.3,
    ) -> list[dict]:
        """
        Pagina automaticamente a busca de ofertas, começando em page=1 e
        seguindo pageInfo.hasNextPage até a API sinalizar que não há mais
        páginas — ou até `max_paginas` como teto de segurança (evita
        loop essencialmente infinito / risco de rate limit em caso de a
        API nunca devolver hasNextPage=false por algum motivo).

        `pausa_entre_paginas_segundos`: pequeno intervalo entre
        requisições consecutivas, para reduzir a chance de esbarrar no
        rate limit (código 10030) ao paginar de forma mais agressiva do
        que antes.
        """
        todos_os_itens: list[dict] = []
        pagina_atual = 1
        atingiu_teto_de_seguranca = True

        while pagina_atual <= max_paginas:
            resultado = self.buscar_ofertas(
                list_type=list_type,
                sort_type=sort_type,
                page=pagina_atual,
                limit=limit,
            )

            if not resultado.nodes:
                logger.info(
                    "Página %s não retornou itens. Encerrando paginação.",
                    pagina_atual,
                )
                atingiu_teto_de_seguranca = False
                break

            todos_os_itens.extend(resultado.nodes)

            if not resultado.has_next_page:
                atingiu_teto_de_seguranca = False
                break

            pagina_atual += 1
            if pagina_atual <= max_paginas and pausa_entre_paginas_segundos > 0:
                time.sleep(pausa_entre_paginas_segundos)

        if atingiu_teto_de_seguranca:
            logger.warning(
                "Atingiu o teto de segurança de %d páginas (listType=%s, "
                "sortType=%s) sem a API sinalizar hasNextPage=false — "
                "pode haver mais produtos disponíveis além do que foi "
                "buscado. Considere aumentar max_paginas se precisar de "
                "mais volume.",
                max_paginas,
                list_type,
                sort_type,
            )

        logger.info(
            "Busca paginada concluída: %d itens no total (listType=%s, sortType=%s).",
            len(todos_os_itens),
            list_type,
            sort_type,
        )
        return todos_os_itens


# ---------------------------------------------------------------------------
# Exemplo de uso via terminal
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Este bloco só roda quando você executa `python shopee_api.py`
    # diretamente. Ele usa as credenciais do seu .env (via config.py) —
    # garanta que o arquivo .env já está preenchido antes de testar.
    import config  # importado aqui de propósito: só é necessário no teste manual

    cliente = ShopeeAffiliateClient(
        app_id=config.SHOPEE_APP_ID,
        app_secret=config.SHOPEE_APP_SECRET,
        api_url=config.SHOPEE_API_URL,
        timeout=config.TIMEOUT_API,
    )

    try:
        ofertas = cliente.buscar_varias_paginas(
            list_type=0, sort_type=5, max_paginas=3, limit=50
        )
        print(f"\nTotal de ofertas recebidas: {len(ofertas)}\n")
        for item in ofertas[:5]:
            print(
                f"- {item.get('productName')} | "
                f"R$ {item.get('priceMin')} | "
                f"desconto {item.get('priceDiscountRate')}% | "
                f"comissão {item.get('commissionRate')}%"
            )
    except ShopeeApiError as erro:
        print(f"Falha ao buscar ofertas: {erro}")
