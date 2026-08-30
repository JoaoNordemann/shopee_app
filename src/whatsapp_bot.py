import json
import random
import time
from datetime import datetime, timedelta
from pathlib import Path

import pyperclip
from apscheduler.schedulers.background import BackgroundScheduler
from playwright.sync_api import sync_playwright
from PySide6.QtCore import QThread, Signal

import config
from data_service import OfertaService
from shopee_api import ShopeeAffiliateClient
from whatsapp_export import escolher_template, gerar_legenda

USER_DATA_DIR = config.BASE_DIR / "playwright_profile"
HISTORY_FILE = config.CACHE_DIR / "bot_history.json"

ESCASSEZ_STRINGS = [
    "⏳ Vai acabar em 10 minutos, corre!",
    "🔥 Estoque quase zerado!",
    "🏃‍♀️ Cupom de Frete Grátis liberado para os primeiros!",
    "🚨 Últimas unidades nesse preço!"
]

def load_history() -> set:
    if HISTORY_FILE.exists():
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()

def save_history(history: set):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(list(history), f)

def get_best_offer(log_callback=None):
    try:
        client = ShopeeAffiliateClient(
            app_id=config.SHOPEE_APP_ID,
            app_secret=config.SHOPEE_APP_SECRET,
            api_url=config.SHOPEE_API_URL,
            timeout=config.TIMEOUT_API,
        )
        service = OfertaService(client=client)
        ofertas = service.atualizar_ofertas()
        
        history = load_history()
        for oferta in ofertas:
            if str(oferta.item_id) not in history:
                history.add(str(oferta.item_id))
                save_history(history)
                return oferta
        return None
    except Exception as e:
        if log_callback:
            log_callback(f"Erro ao buscar ofertas: {str(e)}")
        return None

def send_whatsapp_message(text: str, channel_name: str, log_callback=None):
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch_persistent_context(
                user_data_dir=USER_DATA_DIR,
                headless=False,
                args=["--start-maximized"]
            )
            page = browser.new_page()
            page.goto("https://web.whatsapp.com/", wait_until="networkidle")
            
            try:
                page.get_by_role("button", name="Fechar").click(timeout=5000)
            except:
                pass
                
            botao_canais = page.get_by_role("button", name="Canais")
            botao_canais.wait_for(state="visible", timeout=120000)
            botao_canais.click()
            
            page.get_by_role("button", name=channel_name).click()
            
            caixa_texto = page.locator('div[contenteditable="true"]').first
            caixa_texto.wait_for(state="visible", timeout=30000)
            caixa_texto.click()
            
            pyperclip.copy(text)
            page.keyboard.press("Control+V")
            time.sleep(5)
            page.keyboard.press("Enter")
                
            time.sleep(3)
            browser.close()
            if log_callback:
                log_callback("Mensagem enviada com sucesso no canal.")
    except Exception as e:
        if log_callback:
            log_callback(f"Erro no fluxo do Playwright: {str(e)}")

def job_send_offer(channel_name: str, is_special: bool, log_callback=None):
    if log_callback:
        log_callback("Iniciando busca pela melhor oferta do momento...")
        
    oferta = get_best_offer(log_callback)
    if not oferta:
        if log_callback:
            log_callback("Nenhuma oferta nova encontrada no momento.")
        return
        
    template = escolher_template(config.TEMPLATES_TEXTO_WHATSAPP)
    legenda = gerar_legenda(oferta, template)
    
    if is_special:
        escassez = random.choice(ESCASSEZ_STRINGS)
        legenda = f"{escassez}\n\n{legenda}"
        
    send_whatsapp_message(legenda, channel_name, log_callback)

def job_send_intro_message(channel_name: str, is_payday: bool, is_double_day: bool, log_callback=None):
    motivo = "dia de pagamento" if is_payday else "dia duplo"
    if is_payday and is_double_day:
        motivo = f"{datetime.now().day}.{datetime.now().month} e dia de pagamento"
    elif is_double_day:
        motivo = f"{datetime.now().day}.{datetime.now().month}"
        
    msg = f"Pessoal, hoje é {motivo}, vou mandar muita coisa boa aqui que vai esgotar rápido, fiquem de olho!"
    if log_callback:
        log_callback("Enviando mensagem introdutória do dia especial...")
    send_whatsapp_message(msg, channel_name, log_callback)

def generate_schedule_times(volume: int, is_special: bool = False):
    hoje = datetime.now()
    blocos = [(8, 10), (12, 14), (19, 22)]
    tempos_agendados = []
    
    for _ in range(volume):
        bloco = random.choice(blocos)
        h = random.randint(bloco[0], bloco[1] - 1)
        m = random.randint(1, 59) 
        
        run_date = hoje.replace(hour=h, minute=m, second=random.randint(0, 59))
        if run_date > datetime.now():
            tempos_agendados.append(run_date)
            
    tempos_agendados.sort()
    return tempos_agendados


class WhatsAppBotWorker(QThread):
    log_signal = Signal(str)

    def __init__(self, channel_name: str, mode: str, manual_times: list = None):
        super().__init__()
        self.channel_name = channel_name
        self.mode = mode
        self.manual_times = manual_times or []
        self.scheduler = BackgroundScheduler()
        self.is_running = True

    def _proxy_log(self, msg: str):
        self.log_signal.emit(msg)

    def plan_daily_schedule(self):
        hoje = datetime.now()
        is_payday = (hoje.day == 5)
        is_double_day = (hoje.day == hoje.month)
        is_special = is_payday or is_double_day
        
        if is_special:
            volume = random.randint(15, 20)
            intro_time = hoje.replace(hour=8, minute=0, second=0) + timedelta(minutes=random.randint(1, 15))
            if intro_time > datetime.now():
                self.scheduler.add_job(
                    job_send_intro_message, 'date', run_date=intro_time, 
                    args=[self.channel_name, is_payday, is_double_day, self._proxy_log]
                )
        else:
            volume = random.randint(4, 10)

        tempos = generate_schedule_times(volume, is_special)
        for t in tempos:
            self.scheduler.add_job(
                job_send_offer, 'date', run_date=t, 
                args=[self.channel_name, is_special, self._proxy_log]
            )
            
        self._proxy_log(f"Planejamento diário automático gerou {len(tempos)} postagens para hoje.")

    def run(self):
        USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._proxy_log(f"Automação iniciada em modo: {self.mode.upper()}")
        
        if self.mode == "auto":
            self.plan_daily_schedule()
            self.scheduler.add_job(self.plan_daily_schedule, 'cron', hour=0, minute=1)
        elif self.mode == "manual":
            for t in self.manual_times:
                self.scheduler.add_job(
                    job_send_offer, 'date', run_date=t, 
                    args=[self.channel_name, False, self._proxy_log]
                )
            self._proxy_log(f"{len(self.manual_times)} postagens manuais agendadas.")

        self.scheduler.start()
        
        while self.is_running:
            self.msleep(1000)
            
        self.scheduler.shutdown(wait=False)
        self._proxy_log("Serviço de automação finalizado.")

    def stop(self):
        self.is_running = False