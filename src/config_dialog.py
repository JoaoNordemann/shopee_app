import os
from pathlib import Path

from dotenv import load_dotenv, set_key
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
)

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"


class ConfigDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Configurar API Shopee")
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)
        form_layout = QFormLayout()

        self.input_app_id = QLineEdit()
        self.input_app_secret = QLineEdit()
        self.input_app_secret.setEchoMode(QLineEdit.EchoMode.Password)

        # Carrega os valores atuais em memória, se existirem
        self.input_app_id.setText(os.getenv("SHOPEE_APP_ID", ""))
        self.input_app_secret.setText(os.getenv("SHOPEE_APP_SECRET", ""))

        form_layout.addRow("App ID:", self.input_app_id)
        form_layout.addRow("App Secret:", self.input_app_secret)
        layout.addLayout(form_layout)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.button_box.accepted.connect(self._salvar)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)

    def _salvar(self):
        app_id = self.input_app_id.text().strip()
        app_secret = self.input_app_secret.text().strip()

        if not app_id or not app_secret:
            QMessageBox.warning(self, "Aviso", "Ambos os campos são obrigatórios.")
            return

        if not ENV_PATH.exists():
            ENV_PATH.touch()

        set_key(str(ENV_PATH), "SHOPEE_APP_ID", app_id)
        set_key(str(ENV_PATH), "SHOPEE_APP_SECRET", app_secret)

        load_dotenv(dotenv_path=ENV_PATH, override=True)
        self.accept()