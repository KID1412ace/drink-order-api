import os
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel, Field
from typing import List
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
# 🛡️ 嚴格資安防禦：Pydantic 資料驗證模型
# ==========================================
class OrderItem(BaseModel):
    product_id: int = Field(gt=0, description="商品ID必須大於0")
    product_name: str = Field(min_length=1, max_length=50, description="商品名稱長度限制")
    size: str = Field(pattern="^(中杯\\(M\\)|大杯\\(L\\))$", description="防止偽造容量")
    sugar: str = Field(min_length=1, max_length=10)
    ice: str = Field(min_length=1, max_length=10)
    toppings: str = Field(max_length=20)
    subtotal: int = Field(ge=0, description="小計金額不可為負數")

class Order(BaseModel):
    # 限制姓名長度與格式，初步防禦 XSS 與惡意注入
    user_id: str = Field(min_length=1, max_length=30, pattern=r"^[\w\s\u4e00-\u9fa5]+$", description="僅允許中英文與數字")
    total_price: int = Field(ge=0, le=20000, description="限制單筆訂單總額上限")
    items: List[OrderItem] = Field(min_items=1, max_items=20, description="限制單次最多點20杯，防阻斷服務攻擊")


# ==========================================
# ☁️ 雲端資料庫連線設定 (PostgreSQL)
# ==========================================
DATABASE_URL = os.getenv("DATABASE_URL")

def get_db_connection():
    if not DATABASE_URL:
        raise Exception("未設定 DATABASE_URL 環境變數")
    # 連線到 Render 的 PostgreSQL
    return psycopg2.connect(DATABASE_URL)

def init_db():
    if not DATABASE_URL:
        print("尚未設定 DATABASE_URL，跳過資料庫初始化。")
        return
        
    conn = get_db_connection()
    cursor = conn.cursor()
    # 注意：PostgreSQL 的自動遞增主鍵是 SERIAL，不是 SQLite 的 AUTOINCREMENT
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            id SERIAL PRIMARY KEY,
            user_id VARCHAR(50) NOT NULL,
            total_price INTEGER NOT NULL
        )
    ''')
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

# ==========================================
# 🌐 API 路由邏輯
# ==========================================
@app.get("/")
def read_root():
    return {"message": "雲端飲料點單系統 API (V2.0 Postgres版) 已經成功啟動！"}

@app.get("/client")
def get_client_page():
    return FileResponse("index11.html")

@app.get("/admin")
def get_admin_page():
    return FileResponse("admin.html")

@app.post("/api/orders")
async def create_order(order: Order):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # PostgreSQL 使用 %s 作為參數佔位符 (防禦 SQL Injection)，並透過 RETURNING 取得最新 ID
        cursor.execute(
            "INSERT INTO orders (user_id, total_price) VALUES (%s, %s) RETURNING id", 
            (order.user_id, order.total_price)
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
        raise HTTPException(status_code=500, detail=f"資料庫寫入失敗: {str(e)}")
    finally:
        cursor.close()
        conn.close()

@app.get("/api/orders")
def get_all_orders():
    conn = get_db_connection()
    # 使用 RealDictCursor 讓抓下來的資料直接變成字典 (Dict) 格式
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("SELECT * FROM orders ORDER BY id DESC")
        orders = cursor.fetchall()
        
        result = []
        for order in orders:
            order_dict = dict(order)
            cursor.execute("SELECT * FROM order_items WHERE order_id = %s", (order_dict["id"],))
            items = cursor.fetchall()
            order_dict["items"] = [dict(item) for item in items]
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
        cursor.execute("DELETE FROM order_items WHERE order_id = %s", (order_id,))
        cursor.execute("DELETE FROM orders WHERE id = %s", (order_id,))
        conn.commit()
        return {"status": "success", "message": f"訂單 #{order_id} 已結案"}
    finally:
        cursor.close()
        conn.close()

# ==========================================
# 🤖 LINE Bot 串接設定 (保留自動清理隱藏符號功能)
# ==========================================
raw_token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
raw_secret = os.getenv("LINE_CHANNEL_SECRET")

LINE_CHANNEL_ACCESS_TOKEN = raw_token.strip() if raw_token else None
LINE_CHANNEL_SECRET = raw_secret.strip() if raw_secret else None

if LINE_CHANNEL_ACCESS_TOKEN and LINE_CHANNEL_SECRET:
    line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
    handler = WebhookHandler(LINE_CHANNEL_SECRET)

    @app.post("/callback")
    async def callback(request: Request):
        signature = request.headers.get("X-Line-Signature", "")
        body = await request.body()
        body_str = body.decode("utf-8")
        try:
            handler.handle(body_str, signature)
        except InvalidSignatureError:
            raise HTTPException(status_code=400, detail="無效的簽章")
        return "OK"

    @handler.add(MessageEvent, message=TextMessage)
    def handle_message(event):
        user_msg = event.message.text
        # ⚠️ 記得把下面的網址換成你的 Render 網址！
        my_render_url = "https://drink-order-api.onrender.com"
        
        reply_text = f"歡迎光臨！您剛剛說了：「{user_msg}」\n\n若要點飲料，請點擊下方專屬連結前往點餐喔！👇\n{my_render_url}/client"
        
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply_text)
        )

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=10000, reload=True)