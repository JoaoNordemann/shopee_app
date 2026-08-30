from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTabWidget,
    QSpinBox,
    QTextEdit,
    QScrollArea,
    QDateTimeEdit,
    QMessageBox
)
from datetime import datetime, timedelta

from whatsapp_bot import WhatsAppBotWorker


class AutomationTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.bot_worker = None
        self.manual_times = []
        self.datetime_edits = []
        self._montar_ui()

    def _montar_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        canal_layout = QHBoxLayout()
        canal_layout.addWidget(QLabel("Nome do Canal no WhatsApp:"))
        self.input_canal = QLineEdit()
        self.input_canal.setPlaceholderText("Ex: Canal Most Girls Need")
        self.input_canal.setText("Canal Most Girls Need")
        canal_layout.addWidget(self.input_canal)
        layout.addLayout(canal_layout)

        self.tabs_modo = QTabWidget()
        
        self.tab_auto = QWidget()
        self._montar_aba_automatica(self.tab_auto)
        self.tabs_modo.addTab(self.tab_auto, "Modo Automático")

        self.tab_manual = QWidget()
        self._montar_aba_manual(self.tab_manual)
        self.tabs_modo.addTab(self.tab_manual, "Modo Manual")

        layout.addWidget(self.tabs_modo)

        layout.addWidget(QLabel("Logs do Sistema:"))
        self.log_console = QTextEdit()
        self.log_console.setReadOnly(True)
        self.log_console.setStyleSheet("background-color: #F8F9FA; color: #333; font-family: monospace;")
        layout.addWidget(self.log_console)

    def _montar_aba_automatica(self, parent_widget: QWidget):
        layout = QVBoxLayout(parent_widget)
        
        info = QLabel(
            "O Modo Automático planeja as postagens diariamente com horários variados, "
            "aumentando o volume em dias de pagamento ou datas especiais."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        botoes_layout = QHBoxLayout()
        self.btn_iniciar_auto = QPushButton("▶ Iniciar Automação")
        self.btn_iniciar_auto.clicked.connect(self._iniciar_auto)
        
        self.btn_parar_auto = QPushButton("⏹ Parar")
        self.btn_parar_auto.setEnabled(False)
        self.btn_parar_auto.clicked.connect(self._parar_bot)
        
        botoes_layout.addWidget(self.btn_iniciar_auto)
        botoes_layout.addWidget(self.btn_parar_auto)
        botoes_layout.addStretch()
        
        layout.addLayout(botoes_layout)
        layout.addStretch()

    def _montar_aba_manual(self, parent_widget: QWidget):
        layout = QVBoxLayout(parent_widget)

        spin_layout = QHBoxLayout()
        spin_layout.addWidget(QLabel("Quantidade de postagens hoje:"))
        self.spin_count = QSpinBox()
        self.spin_count.setRange(1, 50)
        self.spin_count.setValue(5)
        spin_layout.addWidget(self.spin_count)
        
        self.btn_gerar_horarios = QPushButton("Gerar Horários")
        self.btn_gerar_horarios.clicked.connect(self._gerar_horarios_manual)
        spin_layout.addWidget(self.btn_gerar_horarios)
        spin_layout.addStretch()
        layout.addLayout(spin_layout)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("QScrollArea { border: 1px solid #ddd; background-color: white; }")
        
        self.scroll_container = QWidget()
        self.scroll_layout = QVBoxLayout(self.scroll_container)
        self.scroll_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll_layout.setSpacing(10)
        self.scroll_area.setWidget(self.scroll_container)
        
        layout.addWidget(self.scroll_area)

        aviso = QLabel("⚠️ Atenção: Mantenha o aplicativo aberto para que os envios sejam realizados.")
        aviso.setStyleSheet("color: red; font-weight: bold;")
        layout.addWidget(aviso)

        botoes_layout = QHBoxLayout()
        self.btn_iniciar_manual = QPushButton("▶ Iniciar Agendamentos")
        self.btn_iniciar_manual.setEnabled(False)
        self.btn_iniciar_manual.clicked.connect(self._iniciar_manual)
        
        self.btn_parar_manual = QPushButton("⏹ Parar")
        self.btn_parar_manual.setEnabled(False)
        self.btn_parar_manual.clicked.connect(self._parar_bot)
        
        botoes_layout.addWidget(self.btn_iniciar_manual)
        botoes_layout.addWidget(self.btn_parar_manual)
        botoes_layout.addStretch()
        
        layout.addLayout(botoes_layout)

    def _log(self, mensagem: str):
        hora = datetime.now().strftime("%H:%M:%S")
        self.log_console.append(f"[{hora}] {mensagem}")

    def _limpar_scroll_layout(self):
        while self.scroll_layout.count():
            item = self.scroll_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def _gerar_horarios_manual(self):
        self._limpar_scroll_layout()
        self.datetime_edits.clear()
        
        count = self.spin_count.value()
        base_time = datetime.now()
        
        for i in range(count):
            base_time += timedelta(minutes=30)
            
            row_widget = QWidget()
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(5, 5, 5, 5)
            
            label = QLabel(f"Postagem {i+1}:")
            label.setFixedWidth(80)
            
            dt_edit = QDateTimeEdit(base_time)
            dt_edit.setDisplayFormat("dd/MM/yyyy HH:mm:ss")
            dt_edit.setCalendarPopup(True)
            
            row_layout.addWidget(label)
            row_layout.addWidget(dt_edit)
            row_layout.addStretch()
            
            self.scroll_layout.addWidget(row_widget)
            self.datetime_edits.append(dt_edit)
            
        self.btn_iniciar_manual.setEnabled(True)
        self._log(f"{count} campos de horário gerados manualmente.")

    def _iniciar_auto(self):
        canal = self.input_canal.text().strip()
        if not canal:
            QMessageBox.warning(self, "Aviso", "Preencha o nome do canal.")
            return

        self._iniciar_thread(canal, modo="auto")
        self.btn_iniciar_auto.setEnabled(False)
        self.btn_parar_auto.setEnabled(True)
        self.input_canal.setEnabled(False)
        self.tabs_modo.setTabEnabled(1, False)

    def _iniciar_manual(self):
        canal = self.input_canal.text().strip()
        if not canal:
            QMessageBox.warning(self, "Aviso", "Preencha o nome do canal.")
            return

        if not self.datetime_edits:
            QMessageBox.warning(self, "Aviso", "Gere os horários antes de iniciar.")
            return

        self.manual_times = [dt_edit.dateTime().toPython() for dt_edit in self.datetime_edits]

        self._iniciar_thread(canal, modo="manual", horarios=self.manual_times)
        
        self.btn_iniciar_manual.setEnabled(False)
        self.btn_gerar_horarios.setEnabled(False)
        for dt_edit in self.datetime_edits:
            dt_edit.setEnabled(False)
            
        self.btn_parar_manual.setEnabled(True)
        self.input_canal.setEnabled(False)
        self.tabs_modo.setTabEnabled(0, False)

    def _iniciar_thread(self, canal: str, modo: str, horarios: list = None):
        self.bot_worker = WhatsAppBotWorker(canal, modo, horarios)
        self.bot_worker.log_signal.connect(self._log)
        self.bot_worker.start()

    def _parar_bot(self):
        if self.bot_worker and self.bot_worker.isRunning():
            self.bot_worker.stop()
            self.bot_worker.wait()
            
        self.btn_iniciar_auto.setEnabled(True)
        self.btn_parar_auto.setEnabled(False)
        
        tem_campos = len(self.datetime_edits) > 0
        self.btn_iniciar_manual.setEnabled(True if tem_campos else False)
        self.btn_gerar_horarios.setEnabled(True)
        for dt_edit in self.datetime_edits:
            dt_edit.setEnabled(True)
            
        self.btn_parar_manual.setEnabled(False)
        
        self.input_canal.setEnabled(True)
        self.tabs_modo.setTabEnabled(0, True)
        self.tabs_modo.setTabEnabled(1, True)
        self._log("Automação interrompida pelo usuário.")