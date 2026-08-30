"""
main.py

Ponto de entrada do aplicativo. Responsável por:
    - Configurar logging (arquivo logs/app.log + console).
    - Carregar e validar config.py, mostrando uma caixa de diálogo clara
      (e encerrando o app) se SHOPEE_APP_ID ou SHOPEE_APP_SECRET
      estiverem faltando no .env — em vez de estourar um traceback cru
      no terminal antes mesmo da janela abrir.
    - Instanciar ShopeeAffiliateClient -> OfertaService -> MainWindow.
    - Subir a QApplication e mostrar a janela.
    - Capturar qualquer exceção não tratada durante a execução (via
      sys.excepthook) e mostrar um diálogo de erro amigável, em vez de o
      app simplesmente fechar sem explicação.

Rode com: python main.py (de dentro da pasta src/, com o .env já
configurado — veja o final da conversa para o passo a passo completo).
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from config_dialog import ConfigDialog

# BASE_DIR calculado de forma independente de config.py (este arquivo
# está em src/, então um nível acima é a raiz do projeto) — assim o
# logging já funciona mesmo se o carregamento de config.py falhar logo
# em seguida.
BASE_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = BASE_DIR / "logs"

logger = logging.getLogger(__name__)

# Tema explícito do app inteiro. Definido aqui (aplicado via
# app.setStyleSheet) para NÃO depender do tema claro/escuro do sistema
# operacional.
#
# Paleta "pastel rosa / clean": fundo off-white/rosa muito pálido, texto
# cinza escuro suave (nunca preto puro), destaque em rosa pastel — pedido
# explícito de redesign visual (glassmorphism leve, minimalista).
COR_FUNDO = "#FFF7FA"
COR_TEXTO = "#4A4A4A"
COR_BORDA_CLARA = "#F3D9E3"
COR_ACCENT = "#FFB6C1"
COR_ACCENT_HOVER = "#FFA6BE"
COR_ACCENT_PRESSED = "#F793AD"
COR_ACCENT_ESCURO = "#E75480"  # rosa pastel mais escuro (destaques de texto)

ESTILO_APP = f"""
QWidget {{
    background-color: {COR_FUNDO};
    color: {COR_TEXTO};
    font-family: "Poppins", "Montserrat", "Segoe UI", Arial, sans-serif;
    font-size: 10pt;
}}

QLabel {{
    color: {COR_TEXTO};
    background-color: transparent;
}}

QLineEdit, QComboBox, QDoubleSpinBox {{
    background-color: #ffffff;
    color: {COR_TEXTO};
    border: 1px solid {COR_BORDA_CLARA};
    border-radius: 10px;
    padding: 6px 10px;
    selection-background-color: {COR_ACCENT};
}}

QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus {{
    border: 1.5px solid {COR_ACCENT};
}}

QComboBox QAbstractItemView {{
    background-color: #ffffff;
    color: {COR_TEXTO};
    selection-background-color: {COR_ACCENT};
    selection-color: {COR_TEXTO};
    border: 1px solid {COR_BORDA_CLARA};
}}

QPushButton {{
    background-color: qlineargradient(
        x1:0, y1:0, x2:1, y2:0,
        stop:0 #FFD1DC, stop:1 {COR_ACCENT}
    );
    color: {COR_TEXTO};
    border: none;
    border-radius: 12px;
    padding: 10px 20px;
    font-weight: 600;
}}

QPushButton:hover {{
    background-color: qlineargradient(
        x1:0, y1:0, x2:1, y2:0,
        stop:0 #FFC2D1, stop:1 {COR_ACCENT_HOVER}
    );
}}

QPushButton:pressed {{
    background-color: {COR_ACCENT_PRESSED};
}}

QPushButton:disabled {{
    background-color: #F5E4EA;
    color: #C7B9BF;
}}

QScrollArea {{
    border: none;
    background-color: transparent;
}}

QScrollBar:vertical {{
    background: #FCEEF3;
    width: 10px;
    border-radius: 5px;
    margin: 0;
}}

QScrollBar::handle:vertical {{
    background: #FFC2D3;
    border-radius: 5px;
    min-height: 24px;
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}

QProgressBar {{
    background-color: #F6E4EA;
    border: none;
    border-radius: 5px;
    text-align: center;
}}

QProgressBar::chunk {{
    background-color: {COR_ACCENT};
    border-radius: 5px;
}}
"""


def configurar_logging() -> None:
    """Configura logging para arquivo (logs/app.log) e console ao mesmo tempo."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    caminho_log = LOG_DIR / "app.log"

    formato = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    handler_arquivo = logging.FileHandler(caminho_log, encoding="utf-8")
    handler_arquivo.setFormatter(formato)

    handler_console = logging.StreamHandler(sys.stdout)
    handler_console.setFormatter(formato)

    raiz = logging.getLogger()
    raiz.setLevel(logging.INFO)
    raiz.addHandler(handler_arquivo)
    raiz.addHandler(handler_console)

    logger.info("Logging configurado. Arquivo de log: %s", caminho_log)


def _excecao_nao_tratada(exc_type, exc_value, exc_traceback) -> None:
    """
    Substitui sys.excepthook: loga qualquer exceção não tratada e mostra
    um diálogo de erro amigável, em vez de deixar o app fechar sem
    explicação nenhuma para o usuário.
    """
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return

    logger.critical(
        "Exceção não tratada.", exc_info=(exc_type, exc_value, exc_traceback)
    )

    QMessageBox.critical(
        None,
        "Erro inesperado",
        "Ocorreu um erro inesperado e a operação não pôde ser concluída.\n\n"
        f"Detalhe: {exc_value}\n\n"
        "Veja logs/app.log para mais informações técnicas.",
    )


def main() -> None:
    configurar_logging()

    app = QApplication(sys.argv)

    # Fusion é o estilo que mais respeita o QSS aplicado (menos
    # "surpresas" de tema nativo do SO sobrepondo nossas cores) — é por
    # isso que a combinação Fusion + stylesheet explícito abaixo garante
    # a mesma aparência em qualquer máquina, independente do tema do
    # Windows estar claro ou escuro.
    app.setStyle("Fusion")
    app.setStyleSheet(ESTILO_APP)

    # Instalado logo após criar a QApplication: precisamos de uma
    # QApplication existente para QMessageBox funcionar corretamente.
    sys.excepthook = _excecao_nao_tratada

    # Interceptação: carrega o .env e verifica se as credenciais existem ANTES de importar o config.py
    load_dotenv(dotenv_path=BASE_DIR / ".env")

    if not os.getenv("SHOPEE_APP_ID") or not os.getenv("SHOPEE_APP_SECRET"):
        dialog = ConfigDialog()
        if dialog.exec() != QDialog.DialogCode.Accepted:
            logger.info("Configuração cancelada. Encerrando.")
            sys.exit(0)

    try:
        import config
    except RuntimeError as exc:
        logger.critical("Falha ao carregar configuração: %s", exc)
        QMessageBox.critical(
            None,
            "Erro de configuração",
            "Não foi possível iniciar o aplicativo por um problema de "
            f"configuração:\n\n{exc}\n\n"
            "Copie o arquivo .env.example para .env e preencha "
            "SHOPEE_APP_ID e SHOPEE_APP_SECRET antes de tentar novamente.",
        )
        sys.exit(1)
    except ModuleNotFoundError as exc:
        # Cenário comum: o ambiente virtual não tem as dependências
        # instaladas (ex: rodou "python main.py" sem antes rodar
        # "pip install -r requirements.txt" dentro do venv ativo).
        logger.critical("Dependência faltando ao carregar configuração: %s", exc)
        QMessageBox.critical(
            None,
            "Dependência faltando",
            "O aplicativo não conseguiu iniciar porque falta um pacote "
            f"Python instalado: {exc.name}\n\n"
            "Verifique se o ambiente virtual está ativado e rode:\n"
            "pip install -r requirements.txt",
        )
        sys.exit(1)

    # Importados só depois de validar config.py com sucesso — todos
    # dependem dele (direta ou indiretamente) e não faz sentido montar
    # nada se as credenciais estiverem faltando.
    from data_service import OfertaService
    from main_window import MainWindow
    from shopee_api import ShopeeAffiliateClient

    logger.info("Configuração carregada. Montando cliente da API e serviço.")

    client = ShopeeAffiliateClient(
        app_id=config.SHOPEE_APP_ID,
        app_secret=config.SHOPEE_APP_SECRET,
        api_url=config.SHOPEE_API_URL,
        timeout=config.TIMEOUT_API,
    )
    oferta_service = OfertaService(client=client)

    janela = MainWindow(oferta_service=oferta_service)
    janela.resize(1000, 700)
    janela.show()

    # Fecha a splash screen do PyInstaller, se estiver em execução
    try:
        import pyi_splash
        pyi_splash.close()
    except ImportError:
        pass

    logger.info("Janela principal exibida. Iniciando loop de eventos da UI.")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()