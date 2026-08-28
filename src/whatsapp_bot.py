import json
import random
import time
from datetime import datetime, timedelta
from pathlib import Path

import pyperclip
from apscheduler.schedulers.blocking import BlockingScheduler
from playwright.sync_api import sync_playwright

import config
from data_service import OfertaService
from shopee_api import ShopeeAffiliateClient
from whatsapp_export import baixar_imagem, escolher_template, gerar_legenda

# Configurações do Bot
USER_DATA_DIR = config.BASE_DIR / "playwright_profile"
HISTORY_FILE = config.CACHE_DIR / "bot_history.json"
IMAGE_TEMP_PATH = config.CACHE_DIR / "temp_offer.jpg"
CHANNEL_NAME = "Canal Most Girls Need"

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

def get_best_offer():
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

def send_whatsapp_message(text: str):
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
        
        page.get_by_role("button", name=CHANNEL_NAME).click()
        
        # Foca diretamente na caixa de texto do canal e envia
        caixa_texto = page.locator('div[contenteditable="true"]').first
        caixa_texto.wait_for(state="visible", timeout=30000)
        caixa_texto.click()
        
        pyperclip.copy(text)
        page.keyboard.press("Control+V")
        time.sleep(5)
        page.keyboard.press("Enter")
            
        time.sleep(3)
        browser.close()

def job_send_offer(is_special: bool):
    oferta = get_best_offer()
    if not oferta:
        return
        
    template = escolher_template(config.TEMPLATES_TEXTO_WHATSAPP)
    legenda = gerar_legenda(oferta, template)
    
    if is_special:
        escassez = random.choice(ESCASSEZ_STRINGS)
        legenda = f"{escassez}\n\n{legenda}"
        
    # Envia a legenda diretamente, sem tentar baixar ou anexar a imagem
    send_whatsapp_message(legenda)

def job_send_intro_message(is_payday: bool, is_double_day: bool):
    motivo = "dia de pagamento" if is_payday else "dia duplo"
    if is_payday and is_double_day:
        motivo = f"{datetime.now().day}.{datetime.now().month} e dia de pagamento"
    elif is_double_day:
        motivo = f"{datetime.now().day}.{datetime.now().month}"
        
    msg = f"Pessoal, hoje é {motivo}, vou mandar muita coisa boa aqui que vai esgotar rápido, fiquem de olho!"
    send_whatsapp_message(msg)

def plan_daily_schedule(scheduler: BlockingScheduler):
    hoje = datetime.now()
    is_payday = (hoje.day == 5)
    is_double_day = (hoje.day == hoje.month)
    is_special = is_payday or is_double_day
    
    if is_special:
        volume = random.randint(15, 20)
        intro_time = hoje.replace(hour=8, minute=0, second=0) + timedelta(minutes=random.randint(1, 15))
        scheduler.add_job(job_send_intro_message, 'date', run_date=intro_time, args=[is_payday, is_double_day])
    else:
        volume = random.randint(4, 10)

    blocos = [
        (8, 10),
        (12, 14),
        (19, 22)
    ]
    
    tempos_agendados = []
    for _ in range(volume):
        bloco = random.choice(blocos)
        h = random.randint(bloco[0], bloco[1] - 1)
        m = random.randint(1, 59) 
        
        run_date = hoje.replace(hour=h, minute=m, second=random.randint(0, 59))
        if run_date > datetime.now():
            tempos_agendados.append(run_date)
            
    tempos_agendados.sort()
    
    for t in tempos_agendados:
        scheduler.add_job(job_send_offer, 'date', run_date=t, args=[is_special])

def main():
    scheduler = BlockingScheduler()
    
    plan_daily_schedule(scheduler)
    scheduler.add_job(plan_daily_schedule, 'cron', hour=0, minute=1, args=[scheduler])
    
    # LINHA DE TESTE: Dispara 1 oferta 10 segundos após iniciar
    # scheduler.add_job(job_send_offer, 'date', run_date=datetime.now() + timedelta(seconds=10), args=[False])
    
    scheduler.start()

if __name__ == "__main__":
    USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    main()