import os
import json
import time
import signal
import requests
import websocket
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()

APP_KEY = os.getenv("KIS_APP_KEY")
APP_SECRET = os.getenv("KIS_APP_SECRET")
CANO = os.getenv("KIS_CANO")
ACNT_PRDT_CD = os.getenv("KIS_ACNT_PRDT_CD", "01")

BASE_URL = "https://openapivts.koreainvestment.com:29443"
WS_URL = "ws://ops.koreainvestment.com:21000"

ACCESS_TOKEN = None
ACCESS_TOKEN_EXPIRES_AT = 0
STOP_REQUESTED = False
CURRENT_WS = None

def request_shutdown(signum=None, frame=None):
    global STOP_REQUESTED, CURRENT_WS

    print("\n종료 요청 수신. 프로그램을 종료합니다.")
    STOP_REQUESTED = True

    if CURRENT_WS is not None:
        try:
            CURRENT_WS.keep_running = False
            CURRENT_WS.close()
        except Exception:
            pass


signal.signal(signal.SIGINT, request_shutdown)
signal.signal(signal.SIGTERM, request_shutdown)

def request_with_retries(method, url, headers=None, params=None, json=None, retries=3, timeout=10):
    for attempt in range(1, retries + 1):
        try:
            if method.lower() == "get":
                res = requests.get(url, headers=headers, params=params, timeout=timeout)
            else:
                res = requests.post(url, headers=headers, json=json, params=params, timeout=timeout)

            res.raise_for_status()
            return res

        except requests.RequestException as e:
            print(f"요청 실패 {attempt}/{retries}: {e}")

            if attempt == retries:
                raise

            time.sleep(0.5 * attempt)


def get_access_token():
    global ACCESS_TOKEN, ACCESS_TOKEN_EXPIRES_AT

    url = f"{BASE_URL}/oauth2/tokenP"

    body = {
        "grant_type": "client_credentials",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET,
    }

    res = request_with_retries("post", url, json=body)
    data = res.json()

    ACCESS_TOKEN = data["access_token"]
    expires_in = int(data.get("expires_in", 86400))
    ACCESS_TOKEN_EXPIRES_AT = time.time() + expires_in

    print("Access token 발급 완료")
    return ACCESS_TOKEN


def ensure_access_token():
    global ACCESS_TOKEN, ACCESS_TOKEN_EXPIRES_AT

    if ACCESS_TOKEN is None or time.time() > ACCESS_TOKEN_EXPIRES_AT - 300:
        return get_access_token()

    return ACCESS_TOKEN


def get_approval_key():
    url = f"{BASE_URL}/oauth2/Approval"

    headers = {
        "content-type": "application/json"
    }

    body = {
        "grant_type": "client_credentials",
        "appkey": APP_KEY,
        "secretkey": APP_SECRET,
    }

    res = request_with_retries("post", url, headers=headers, json=body)
    print("WebSocket approval_key 발급 완료")
    return res.json()["approval_key"]


def get_current_price(code):
    token = ensure_access_token()

    url = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price"

    headers = {
        "authorization": f"Bearer {token}",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET,
        "tr_id": "FHKST01010100",
    }

    params = {
        "fid_cond_mrkt_div_code": "J",
        "fid_input_iscd": code,
    }

    res = request_with_retries("get", url, headers=headers, params=params)
    output = res.json()["output"]

    return {
        "current": int(output["stck_prpr"]),
        "open": int(output["stck_oprc"]),
        "high": int(output["stck_hgpr"]),
        "low": int(output["stck_lwpr"]),
    }


def get_daily_prices(code):
    token = ensure_access_token()

    url = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-daily-price"

    headers = {
        "authorization": f"Bearer {token}",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET,
        "tr_id": "FHKST01010400",
    }

    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": code,
        "FID_PERIOD_DIV_CODE": "D",
        "FID_ORG_ADJ_PRC": "0",
    }

    res = request_with_retries("get", url, headers=headers, params=params)
    return res.json()["output"]


def get_stock_balance(code):
    token = ensure_access_token()

    url = f"{BASE_URL}/uapi/domestic-stock/v1/trading/inquire-balance"

    headers = {
        "authorization": f"Bearer {token}",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET,
        "tr_id": "VTTC8434R",
        "custtype": "P",
    }

    params = {
        "CANO": CANO,
        "ACNT_PRDT_CD": ACNT_PRDT_CD,
        "AFHR_FLPR_YN": "N",
        "OFL_YN": "",
        "INQR_DVSN": "02",
        "UNPR_DVSN": "01",
        "FUND_STTL_ICLD_YN": "N",
        "FNCG_AMT_AUTO_RDPT_YN": "N",
        "PRCS_DVSN": "01",
        "CTX_AREA_FK100": "",
        "CTX_AREA_NK100": "",
    }

    res = request_with_retries("get", url, headers=headers, params=params)
    data = res.json()

    if data.get("rt_cd") != "0":
        print("잔고 조회 실패:", data)
        return 0, 0

    stocks = data.get("output1", [])

    for stock in stocks:
        if stock.get("pdno") == code:
            qty = int(stock.get("hldg_qty", 0))
            avg_price = float(stock.get("pchs_avg_pric", 0))
            return qty, avg_price

    return 0, 0


def buy_stock(code, qty):
    token = ensure_access_token()

    url = f"{BASE_URL}/uapi/domestic-stock/v1/trading/order-cash"

    headers = {
        "authorization": f"Bearer {token}",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET,
        "tr_id": "VTTC0802U",
        "custtype": "P",
    }

    body = {
        "CANO": CANO,
        "ACNT_PRDT_CD": ACNT_PRDT_CD,
        "PDNO": code,
        "ORD_DVSN": "01",
        "ORD_QTY": str(qty),
        "ORD_UNPR": "0",
    }

    res = request_with_retries("post", url, headers=headers, json=body)
    result = res.json()

    print("매수 결과:", result)
    return result


def sell_stock(code, qty):
    token = ensure_access_token()

    url = f"{BASE_URL}/uapi/domestic-stock/v1/trading/order-cash"

    headers = {
        "authorization": f"Bearer {token}",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET,
        "tr_id": "VTTC0801U",
        "custtype": "P",
    }

    body = {
        "CANO": CANO,
        "ACNT_PRDT_CD": ACNT_PRDT_CD,
        "PDNO": code,
        "ORD_DVSN": "01",
        "ORD_QTY": str(qty),
        "ORD_UNPR": "0",
    }

    res = request_with_retries("post", url, headers=headers, json=body)
    result = res.json()

    print("매도 결과:", result)
    return result


def is_market_time():
    now = datetime.now(ZoneInfo("Asia/Seoul")).time()
    return dtime(9, 0) <= now <= dtime(15, 20)


class WebSocketTradingBot:
    def __init__(
        self,
        code="005930",
        qty=1,
        k=0.3,
        take_profit=0.005,
        stop_loss=-0.005,
        cooldown=10,
        max_position=3,
        add_buy_rate=0.002
    ):
        self.code = code
        self.qty = qty
        self.k = k
        self.take_profit = take_profit
        self.stop_loss = stop_loss
        self.cooldown = cooldown
        self.max_position = max_position
        self.add_buy_rate = add_buy_rate

        self.target_price = 0
        self.holding = False
        self.holding_qty = 0
        self.buy_price = 0
        self.last_sell_time = 0
        self.ordering = False
        self.last_processed_time = 0
        self.process_interval = 1.0

    def initialize_strategy(self):
        daily_prices = get_daily_prices(self.code)
        print("일봉 조회 완료")
        yesterday = daily_prices[1]

        yesterday_high = int(yesterday["stck_hgpr"])
        yesterday_low = int(yesterday["stck_lwpr"])

        today_info = get_current_price(self.code)
        print("현재가 조회 완료")
        today_open = today_info["open"]

        self.target_price = int(today_open + (yesterday_high - yesterday_low) * self.k)

        balance_qty, avg_price = get_stock_balance(self.code)

        self.holding_qty = balance_qty
        self.holding = balance_qty > 0

        if self.holding:
            self.buy_price = int(avg_price)
        else:
            self.buy_price = 0

        print("=== WebSocket 실시간 자동매매 시작 ===")
        print(f"종목코드: {self.code}")
        print(f"어제 고가: {yesterday_high}")
        print(f"어제 저가: {yesterday_low}")
        print(f"오늘 시가: {today_open}")
        print(f"변동성 돌파 목표가: {self.target_price}")
        print(f"현재 보유수량: {self.holding_qty}")
        print(f"평균 매수가: {self.buy_price}")
        print(f"익절 기준: {self.take_profit * 100}%")
        print(f"손절 기준: {self.stop_loss * 100}%")

    def sync_balance(self):
        balance_qty, avg_price = get_stock_balance(self.code)

        self.holding_qty = balance_qty
        self.holding = balance_qty > 0

        if self.holding:
            self.buy_price = int(avg_price)
        else:
            self.buy_price = 0

    def handle_price(self, current_price):

        now = time.time()

        if now - self.last_processed_time < self.process_interval:
            return

        self.last_processed_time = now

        now_text = datetime.now(
            ZoneInfo("Asia/Seoul")
        ).strftime("%H:%M:%S")

        print(f"[{now_text}] 현재가 처리 시작: {current_price}")

        if not is_market_time():
            print("장 시간이 아니어서 매매 판단 안 함")
            return

        if self.ordering:
            return

        now_text = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%H:%M:%S")


        if self.holding_qty < self.max_position:
            enough_cooldown = time.time() - self.last_sell_time > self.cooldown

            if self.holding_qty == 0:
                buy_target_price = self.target_price
                buy_reason = "1차 매수: 변동성 돌파 목표가"

            else:
                buy_target_price = int(self.buy_price * (1 + self.add_buy_rate))
                buy_reason = f"추가매수: 평균가 대비 +{self.add_buy_rate * 100}%"

        if self.holding_qty > 0 and self.buy_price > 0:
            profit_rate = (
                current_price - self.buy_price
            ) / self.buy_price

            print(
                f"현재가: {current_price}, "
                f"목표가: {self.target_price}, "
                f"보유중: {self.holding}, "
                f"보유수량: {self.holding_qty}/{self.max_position}, "
                f"평균가: {self.buy_price}, "
                f"매수 기준가: {buy_target_price}, "
                f"수익률: {profit_rate * 100:.3f}%"
            )
        else:
            print(
                f"현재가: {current_price}, "
                f"목표가: {self.target_price}, "
                f"보유중: {self.holding}, "
                f"보유수량: {self.holding_qty}/{self.max_position}, "
                f"평균가: {self.buy_price}, "
                f"매수 기준가: {buy_target_price}"
            )

            if current_price >= buy_target_price and enough_cooldown:
                print(f"{buy_reason} 충족")

                self.ordering = True

                try:
                    result = buy_stock(self.code, self.qty)

                    if result.get("rt_cd") == "0":
                        print("매수 주문 성공")
                        time.sleep(1)
                        self.sync_balance()

                        print(
                            f"잔고 반영 완료. "
                            f"보유수량: {self.holding_qty}, "
                            f"평균가: {self.buy_price}"
                        )

                    else:
                        print("매수 주문 실패:", result)

                except Exception as e:
                    print("매수 주문 중 예외 발생:", e)

                finally:
                    self.ordering = False

        if self.holding:
            if self.buy_price <= 0:
                print("매수가 정보가 없습니다. 잔고를 다시 동기화합니다.")
                self.sync_balance()
                return

            profit_rate = (current_price - self.buy_price) / self.buy_price

            if profit_rate >= self.take_profit:
                print("익절 조건 충족")

                self.ordering = True

                try:
                    result = sell_stock(self.code, self.holding_qty)

                    if result.get("rt_cd") == "0":
                        print("익절 매도 주문 성공")
                        time.sleep(1)
                        self.sync_balance()
                        self.last_sell_time = time.time()

                        if not self.holding:
                            print("익절 매도 후 미보유 상태 확인")

                    else:
                        print("익절 매도 실패:", result)

                except Exception as e:
                    print("익절 매도 중 예외 발생:", e)

                finally:
                    self.ordering = False

            elif profit_rate <= self.stop_loss:
                print("손절 조건 충족")

                self.ordering = True

                try:
                    result = sell_stock(self.code, self.holding_qty)

                    if result.get("rt_cd") == "0":
                        print("손절 매도 주문 성공")
                        time.sleep(1)
                        self.sync_balance()
                        self.last_sell_time = time.time()

                        if not self.holding:
                            print("손절 매도 후 미보유 상태 확인")

                    else:
                        print("손절 매도 실패:", result)

                except Exception as e:
                    print("손절 매도 중 예외 발생:", e)

                finally:
                    self.ordering = False


def on_open(ws):
    print("WebSocket 연결 완료")

    subscribe = {
        "header": {
            "approval_key": ws.approval_key,
            "custtype": "P",
            "tr_type": "1",
            "content-type": "utf-8",
        },
        "body": {
            "input": {
                "tr_id": "H0STCNT0",
                "tr_key": ws.bot.code,
            }
        },
    }

    ws.send(json.dumps(subscribe))
    print("실시간 체결가 구독 요청 완료")


def on_message(ws, message):
    if message.startswith("0|"):
        try:
            data = message.split("|")
            raw_data = data[3]
            fields = raw_data.split("^")

            current_price = int(fields[2])
            #print(f"WebSocket 현재가 수신: {current_price}")

            ws.bot.handle_price(current_price)

        except Exception as e:
            print("WebSocket 메시지 처리 오류:", e)
            print("원본 메시지:", message)

    else:
        try:
            data = json.loads(message)
            tr_id = data.get("header", {}).get("tr_id")

            if tr_id == "PINGPONG":
                return

            print("WebSocket JSON 응답:", data)

        except Exception:
            print("WebSocket 응답:", message)


def on_error(ws, error):
    if STOP_REQUESTED:
        print("WebSocket 정상 종료 중")
        return

    print("WebSocket 에러:", error)


def on_close(ws, close_status_code, close_msg):
    if STOP_REQUESTED:
        print("WebSocket 연결 종료 완료")
    else:
        print("WebSocket 종료:", close_status_code, close_msg)


def validate_env():
    missing = []

    if not APP_KEY:
        missing.append("KIS_APP_KEY")
    if not APP_SECRET:
        missing.append("KIS_APP_SECRET")
    if not CANO:
        missing.append("KIS_CANO")

    if missing:
        raise ValueError(f".env에 다음 값이 없습니다: {missing}")


def run_websocket_trading_bot(
    code="005930",
    qty=1,
    k=0.5,
    take_profit=0.005,
    stop_loss=-0.005,
    cooldown=60,
    max_position=3,
    add_buy_rate=0.002,
):
    global STOP_REQUESTED, CURRENT_WS

    validate_env()
    ensure_access_token()

    bot = WebSocketTradingBot(
        code=code,
        qty=qty,
        k=k,
        take_profit=take_profit,
        stop_loss=stop_loss,
        cooldown=cooldown,
        max_position=max_position,
        add_buy_rate=add_buy_rate,
    )

    bot.initialize_strategy()

    while not STOP_REQUESTED:
        try:
            approval_key = get_approval_key()

            ws = websocket.WebSocketApp(
                WS_URL,
                on_open=on_open,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close,
            )

            ws.approval_key = approval_key
            ws.bot = bot

            CURRENT_WS = ws

            ws.run_forever(
                ping_interval=30,
                ping_timeout=10
            )

            CURRENT_WS = None

        except KeyboardInterrupt:
            request_shutdown()
            break

        except Exception as e:
            if STOP_REQUESTED:
                break
            print("WebSocket 재연결 필요:", e)

        if STOP_REQUESTED:
            break

        print("5초 후 재연결")
        for _ in range(5):
            if STOP_REQUESTED:
                break
            time.sleep(1)

    print("프로그램 종료 완료")


if __name__ == "__main__":
    run_websocket_trading_bot(
        code="005930",
        qty=1,
        k=0.2,
        take_profit=0.002,
        stop_loss=-0.002,
        cooldown=10,
        max_position=3,
        add_buy_rate=0.002,
    )