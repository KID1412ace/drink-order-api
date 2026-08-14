import os
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional
import uvicorn
from fastapi.middleware.cors import CORSMiddleware 
from fastapi.responses import FileResponse
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==========================================
# 🛡️ Pydantic 資料驗證模型 (新增 line_uid)
# ==========================================
class OrderItem(BaseModel):
    product_id: int = Field(gt=0)
    product_name: str = Field(min_length=1, max_length=50)
    size: str = Field(pattern="^(中杯\\(M\\)|大杯\\(L\\))$")
    sugar: str = Field(min_length=1, max_length=10)
    ice: str = Field(min_length=1, max_length=10)
    toppings: str = Field(max_length=20)
    subtotal: int = Field(ge=0)

class Order(BaseModel):
    user_id: str = Field(min_length=1, max_length=30, pattern=r"^[\w\s\u4e00-\u9fa5]+$")
    line_uid: Optional[str] = Field(default=None, description="LINE 專屬辨識碼") # 新增這行
    total_price: int = Field(ge=0, le=20000)
    items: List[OrderItem] = Field(min_items=1, max_items=20)

# ==========================================
# ☁️ 雲端資料庫設定
# ==========================================
DATABASE_URL = os.getenv("DATABASE_URL")

def get_db_connection():
    if not DATABASE_URL:
        raise Exception("未設定 DATABASE_URL 環境變數")
    return psycopg2.connect(DATABASE_URL)

def init_db():
    if not DATABASE_URL:
        return
    conn = get_db_connection()
    cursor = conn.cursor()
    # 建立基本表單
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            id SERIAL PRIMARY KEY,
            user_id VARCHAR(50) NOT NULL,
            total_price INTEGER NOT NULL
        )
    ''')
    # 動態擴充欄位 (如果舊版資料庫沒有 line_uid，系統會自動補上)
    cursor.execute('ALTER TABLE orders ADD COLUMN IF NOT EXISTS line_uid VARCHAR(50);')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS order_items (
            id SERIAL PRIMARY KEY,
            order_id INTEGER NOT NULL,
            product_name VARCHAR(50) NOT NULL,
            size VARCHAR(20),
            sugar VARCHAR(20),
            ice VARCHAR(20),
            subtotal INTEGER NOT NULL
        )
    ''')
    conn.commit()
    cursor.close()
    conn.close()

init_db()

# LINE Bot 初始化
raw_token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
raw_secret = os.getenv("LINE_CHANNEL_SECRET")
LINE_CHANNEL_ACCESS_TOKEN = raw_token.strip() if raw_token else None
LINE_CHANNEL_SECRET = raw_secret.strip() if raw_secret else None

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN) if LINE_CHANNEL_ACCESS_TOKEN else None
handler = WebhookHandler(LINE_CHANNEL_SECRET) if LINE_CHANNEL_SECRET else None

# ==========================================
# 🌐 API 路由邏輯
# ==========================================
@app.get("/")
def read_root(): return {"message": "雲端飲料點單系統 V2.1 (支援 LINE 推播) 啟動！"}

@app.get("/client")
def get_client_page(): return FileResponse("index11.html")

@app.get("/admin")
def get_admin_page(): return FileResponse("admin.html")

@app.post("/api/orders")
async def create_order(order: Order):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # 將 line_uid 一併寫入資料庫
        cursor.execute(
            "INSERT INTO orders (user_id, total_price, line_uid) VALUES (%s, %s, %s) RETURNING id", 
            (order.user_id, order.total_price, order.line_uid)
        )
        new_order_id = cursor.fetchone()[0]
        
        for item in order.items:
            cursor.execute(
                "INSERT INTO order_items (order_id, product_name, size, sugar, ice, subtotal) VALUES (%s, %s, %s, %s, %s, %s)",
                (new_order_id, item.product_name, item.size, item.sugar, item.ice, item.subtotal)
            )
        conn.commit()
        return {"message": "訂單建立並儲存成功！", "order_id": new_order_id}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"寫入失敗: {str(e)}")
    finally:
        cursor.close()
        conn.close()

@app.get("/api/orders")
def get_all_orders():
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("SELECT * FROM orders ORDER BY id DESC")
        orders = cursor.fetchall()
        result = []
        for order in orders:
            order_dict = dict(order)
            cursor.execute("SELECT * FROM order_items WHERE order_id = %s", (order_dict["id"],))
            order_dict["items"] = [dict(item) for cursor_item in cursor.fetchall() for item in [cursor_item]]
            result.append(order_dict)
        return {"status": "success", "total_orders": len(result), "data": result}
    finally:
        cursor.close()
        conn.close()

@app.delete("/api/orders/{order_id}")
def complete_order(order_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # 結案前，先把該訂單的顧客姓名與 LINE UID 抓出來
        cursor.execute("SELECT user_id, line_uid FROM orders WHERE id = %s", (order_id,))
        row = cursor.fetchone()
        
        # 執行刪除 (結案)
        cursor.execute("DELETE FROM order_items WHERE order_id = %s", (order_id,))
        cursor.execute("DELETE FROM orders WHERE id = %s", (order_id,))
        conn.commit()

        # 🚀 觸發 O2O LINE 推播通知
        if row and row[1] and line_bot_api:
            user_name = row[0]
            line_uid = row[1]
            push_text = f"🔔 通知：{user_name} 您好，您的訂單 #{order_id} 已經製作完成囉！請前往櫃檯取餐 🧋"
            try:
                line_bot_api.push_message(line_uid, TextSendMessage(text=push_text))
            except Exception as e:
                print(f"推播失敗: {e}") # 避免因為推播失敗導致系統崩潰

        return {"status": "success", "message": f"訂單 #{order_id} 已結案"}
    finally:
        cursor.close()
        conn.close()

# ==========================================
# 🤖 LINE Webhook
# ==========================================
@app.post("/callback")
async def callback(request: Request):
    if not handler: return "OK"
    signature = request.headers.get("X-Line-Signature", "")
    body_str = (await request.body()).decode("utf-8")
    try:
        handler.handle(body_str, signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="無效的簽章")
    return "OK"

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_msg = event.message.text
    user_line_id = event.source.user_id # 抓取顧客真正的 LINE ID
    
    my_render_url = "https://drink-order-api.onrender.com"
    # 將 ID 當作參數 (uid) 綁在網址後面
    reply_text = f"歡迎光臨！若要點飲料，請點擊專屬連結前往點餐喔！👇\n{my_render_url}/client?uid={user_line_id}"
    
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=10000, reload=True)