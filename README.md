# Ofertas Shopee - Afiliados

Aplicativo desktop e bot autônomo em Python para gerenciar e divulgar ofertas de afiliado da Shopee. O sistema busca produtos via API GraphQL oficial, calcula um score de rentabilidade, exibe os resultados em uma interface gráfica e realiza postagens automatizadas em canais do WhatsApp.

## Funcionalidades

- **Motor de Ofertas:** Busca automática na Shopee Open API (Affiliate) com paginação completa e cálculo de score de rentabilidade (0 a 10) combinando avaliação, vendas, desconto, comissão e critérios manuais (utilidade e facilidade de conteúdo).
- **Interface de Curadoria (UI):** Grid de cards com filtro por texto, categoria e score mínimo, paginação em memória e cache local (JSON) para inicialização rápida.
- **Bot Autônomo para WhatsApp:** Integração via Playwright com sessão persistente, eliminando a necessidade de escanear o QR Code a cada uso.
- **Postagem Otimizada em Texto:** Envio direto da legenda e link da oferta (sem anexo de imagens) via `pyperclip` (área de transferência) para evitar gargalos de lentidão e timeouts do WhatsApp Web.
- **Agendamento Humanizado (Jitter):** Orquestração com `APScheduler` para distribuir postagens em horários aleatórios dentro de janelas predefinidas (08h-10h, 12h-14h e 19h-22h).
- **Dias Especiais:** Lógica para aumentar o volume de envios e injetar gatilhos de escassez em dias de pagamento (dia 5) e datas duplas (ex: 9/9, 10/10).
- **Controle Anti-Repetição:** Registro em cache (`bot_history.json`) para garantir que uma mesma oferta não seja divulgada duas vezes.

## Stack

- Python 3.11+
- PySide6 (interface gráfica)
- Playwright & APScheduler (automação web e agendamento)
- requests & Pillow (integração de rede e manipulação de mídia)
- pyperclip (transferência de texto)
- python-dotenv (configurações locais)
- PyInstaller (empacotamento executável)

## Configuração

1. Clone o repositório e crie um ambiente virtual:
   ```bash
   python -m venv .venv
   .venv\Scripts\activate      # Windows
   source .venv/bin/activate   # Linux/Mac
   ```
2. Instale as dependências e o navegador do Playwright:
   ```bash
   pip install -r requirements.txt
   playwright install chromium
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
python src/main.py
```

## Uso bot autônomo de postagem (mantenha o terminal aberto):

```bash
python src/whatsapp_bot.py
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
├── build.spec            # configuração do PyInstaller
├── requirements.txt
├── assets/               # ícone do executável
├── cache/                # cache local (ofertas, histórico do bot e imagens)
├── logs/                 # logs de execução
├── playwright_profile/   # sessão persistente do WhatsApp Web (gerado automaticamente)
└── src/
    ├── main.py             # ponto de entrada da interface
    ├── whatsapp_bot.py     # bot autônomo de postagem
    ├── config.py           # carregamento de configuração/.env
    ├── shopee_api.py       # cliente da API GraphQL
    ├── scoring.py          # modelo de cálculo de score
    ├── data_service.py     # orquestração de busca, score e cache
    ├── whatsapp_export.py  # geração de legenda e exportação
    ├── main_window.py      # janela principal (UI)
    ├── offer_card.py       # card de produto (UI)
    └── theme.py            # paleta de cores da interface
```

## Aviso

Este projeto usa credenciais de API pessoais. Nunca compartilhe o arquivo `.env` nem o commite no repositório.
