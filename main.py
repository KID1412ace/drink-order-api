from fastapi import FastAPI
from pydantic import BaseModel
from typing import List
import uvicorn
import sqlite3
from fastapi.middleware.cors import CORSMiddleware 
from fastapi.responses import FileResponse  # 🌟 新增這行

app = FastAPI()

# 設定 CORS 允許所有來源連線
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
# 🌟 新增：讓客人連線到 /client 時，伺服器回傳 index.html
@app.get("/client")
def get_client_page():
    return FileResponse("index11.html")

# 🌟 新增：讓老闆連線到 /admin 時，伺服器回傳 admin.html
@app.get("/admin")
def get_admin_page():
    return FileResponse("admin.html")

@app.post("/api/orders")
async def create_order(order: Order):
    conn = sqlite3.connect("drinks.db")
    cursor = conn.cursor()
    
    cursor.execute(
        "INSERT INTO orders (user_id, total_price) VALUES (?, ?)", 
        (order.user_id, order.total_price)
    )
    new_order_id = cursor.lastrowid 
    
    for item in order.items:
        cursor.execute(
            "INSERT INTO order_items (order_id, product_name, size, sugar, ice, subtotal) VALUES (?, ?, ?, ?, ?, ?)",
            (new_order_id, item.product_name, item.size, item.sugar, item.ice, item.subtotal)
        )
    
    conn.commit()
    conn.close()
    
    print(f"成功將顧客 {order.user_id} 的訂單存入資料庫！訂單編號為 #{new_order_id}")
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

# ==========================================
# 🌟 刪除訂單的 API 必須放在這裡 (uvicorn.run 的上方)！
# ==========================================
@app.delete("/api/orders/{order_id}")
def complete_order(order_id: int):
    conn = sqlite3.connect("drinks.db")
    cursor = conn.cursor()
    
    cursor.execute("DELETE FROM orders WHERE id = ?", (order_id,))
    cursor.execute("DELETE FROM order_items WHERE order_id = ?", (order_id,))
    
    conn.commit()
    conn.close()
    
    print(f"✅ 訂單 #{order_id} 已經製作完成並結案！")
    return {"status": "success", "message": f"訂單 #{order_id} 已結案"}

# 這行一定要在整個檔案的最下面
if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)