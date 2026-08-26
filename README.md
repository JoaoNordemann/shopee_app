# Ofertas Shopee - Afiliados

Aplicativo desktop em Python/PySide6 para gerenciar ofertas de afiliado da Shopee. Busca produtos via API GraphQL oficial de afiliados, calcula um score de rentabilidade ponderado para cada oferta, exibe os resultados em cards visuais e gera pacotes prontos (imagem + legenda) para divulgação no WhatsApp.

## Funcionalidades

- Busca automática de ofertas na Shopee Open API (Affiliate), com paginação completa e múltiplas combinações de categoria/ordenação
- Cálculo de score de rentabilidade (0 a 10) combinando avaliação, volume de vendas, desconto, comissão, reputação da loja e critérios editáveis manualmente (utilidade e facilidade de conteúdo)
- Grid de cards com imagem, preço, desconto, comissão e score, com filtro por texto, categoria e score mínimo
- Paginação da exibição (50 itens por página) para manter a interface leve mesmo com milhares de produtos
- Botões de compartilhamento: cópia de imagem e de legenda (com variação automática de texto) prontos para colar no WhatsApp
- Cache local em JSON para abertura rápida sem depender da API a cada execução
- Empacotamento em executável único via PyInstaller

## Stack

- Python 3.11+
- PySide6 (interface gráfica)
- requests (integração com a API)
- Pillow (tratamento de imagem)
- python-dotenv (configuração)
- PyInstaller (empacotamento)

## Configuração

1. Clone o repositório e crie um ambiente virtual:
   ```bash
   python -m venv .venv
   .venv\Scripts\activate      # Windows
   source .venv/bin/activate   # Linux/Mac
   ```
2. Instale as dependências:
   ```bash
   pip install -r requirements.txt
   ```
3. Copie `.env.example` para `.env` na raiz do projeto e preencha suas credenciais da Shopee Open API:
   ```
   SHOPEE_APP_ID=
   SHOPEE_APP_SECRET=
   SHOPEE_API_URL=https://open-api.affiliate.shopee.com.br/graphql
   ```

## Uso

```bash
cd src
python main.py
```

## Empacotamento (.exe)

```bash
pyinstaller build.spec
```

O executável gerado (`dist/ShopeeAfiliados.exe`) espera o arquivo `.env` na mesma pasta — as credenciais nunca ficam embutidas no binário.

## Estrutura do projeto

```
shopee_app/
├── .env                  # credenciais (não versionado)
├── build.spec             # configuração do PyInstaller
├── requirements.txt
├── assets/                # ícone do executável
├── cache/                 # cache local (ofertas e imagens)
├── logs/                  # logs de execução
└── src/
    ├── main.py             # ponto de entrada
    ├── config.py           # carregamento de configuração/.env
    ├── shopee_api.py       # cliente da API GraphQL
    ├── scoring.py          # modelo de cálculo de score
    ├── data_service.py     # orquestração de busca, score e cache
    ├── whatsapp_export.py  # geração de legenda e exportação
    ├── main_window.py      # janela principal
    ├── offer_card.py       # card de produto
    └── theme.py            # paleta de cores da interface
```

## Aviso

Este projeto usa credenciais de API pessoais. Nunca compartilhe o arquivo `.env` nem o commite no repositório.
