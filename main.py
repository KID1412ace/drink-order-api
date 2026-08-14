import os
from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
from typing import List
import uvicorn
import sqlite3
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

def init_db():
    conn = sqlite3.connect("drinks.db")
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            total_price INTEGER
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS order_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER,
            product_name TEXT,
            size TEXT,
            sugar TEXT,
            ice TEXT,
            subtotal INTEGER
        )
    ''')
    conn.commit()
    conn.close()

init_db()

class OrderItem(BaseModel):
    product_id: int
    product_name: str
    size: str
    sugar: str
    ice: str
    toppings: str
    subtotal: int

class Order(BaseModel):
    user_id: str
    total_price: int
    items: List[OrderItem]

@app.get("/")
def read_root():
    return {"message": "飲料點單系統 API 已經成功啟動！"}

@app.get("/client")
def get_client_page():
    return FileResponse("index11.html")

@app.get("/admin")
def get_admin_page():
    return FileResponse("admin.html")

@app.post("/api/orders")
async def create_order(order: Order):
    conn = sqlite3.connect("drinks.db")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO orders (user_id, total_price) VALUES (?, ?)", (order.user_id, order.total_price))
    new_order_id = cursor.lastrowid 
    for item in order.items:
        cursor.execute(
            "INSERT INTO order_items (order_id, product_name, size, sugar, ice, subtotal) VALUES (?, ?, ?, ?, ?, ?)",
            (new_order_id, item.product_name, item.size, item.sugar, item.ice, item.subtotal)
        )
    conn.commit()
    conn.close()
    return {"message": "訂單建立並儲存成功！", "order_id": new_order_id}

@app.get("/api/orders")
def get_all_orders():
    conn = sqlite3.connect("drinks.db")
    conn.row_factory = sqlite3.Row 
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM orders")
    orders = cursor.fetchall()
    result = []
    for order in orders:
        order_dict = dict(order)
        cursor.execute("SELECT * FROM order_items WHERE order_id = ?", (order_dict["id"],))
        items = cursor.fetchall()
        order_dict["items"] = [dict(item) for item in items]
        result.append(order_dict)
    conn.close()
    return {"status": "success", "total_orders": len(result), "data": result}

@app.delete("/api/orders/{order_id}")
def complete_order(order_id: int):
    conn = sqlite3.connect("drinks.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM orders WHERE id = ?", (order_id,))
    cursor.execute("DELETE FROM order_items WHERE order_id = ?", (order_id,))
    conn.commit()
    conn.close()
    return {"status": "success", "message": f"訂單 #{order_id} 已結案"}

# ==========================================
# 🌟 加入 .strip() 自動清除隱藏符號的防禦機制
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