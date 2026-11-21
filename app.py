import streamlit as st
import pandas as pd
import requests
from datetime import datetime

st.set_page_config(page_title="亚马逊2027统一月度仓储费预测", layout="wide")
st.title("📦 亚马逊2027统一月度仓储费预测 (库龄阶梯版)")
st.caption("规则生效：2027年2月1日起 | 费率基于你提供的官方阶梯表 | 汇率每日缓存更新")

# ================= 汇率获取 =================
@st.cache_data(ttl=3600)  # 缓存1小时，避免频繁请求
def get_usd_cny_rate():
    try:
        # open.er-api.com 免费、无需Key、每日更新
        resp = requests.get("https://open.er-api.com/v6/latest/USD", timeout=5)
        data = resp.json()
        if data.get("result") == "success":
            return data["rates"]["CNY"]
    except Exception:
        pass
    return 7.20  # 备用默认值

usd_cny = get_usd_cny_rate()
st.sidebar.info(f"💱 当前参考汇率：1 USD ≈ **{usd_cny:.4f}** CNY")

# ================= 输入参数 =================
st.sidebar.header("📐 1. 产品尺寸与分类")

size_mode = st.sidebar.radio("尺寸输入方式", ["输入长宽高 (英寸)", "直接输入单件体积 (立方英尺)"])

if size_mode == "输入长宽高 (英寸)":
    col_a, col_b, col_c = st.sidebar.columns(3)
    length = col_a.number_input("长 (in)", min_value=0.1, value=10.0, step=1.0)
    width = col_b.number_input("宽 (in)", min_value=0.1, value=8.0, step=1.0)
    height = col_c.number_input("高 (in)", min_value=0.1, value=4.0, step=1.0)
    unit_volume = (length * width * height) / 1728.0
    st.sidebar.caption(f"单件体积 = {length}×{width}×{height} ÷ 1728 = **{unit_volume:.4f} cu.ft.**")
    
    # 亚马逊长期仓储费有“不足1按1”的规则，月度仓储费通常按实际体积，这里提供选项
    round_up = st.sidebar.checkbox("体积不足 1 cu.ft. 时按 1 cu.ft. 计费", value=False)
    if round_up and unit_volume < 1.0:
        unit_volume = 1.0
        st.sidebar.warning("已启用：体积进位为 1.0 cu.ft.")
else:
    unit_volume = st.sidebar.number_input("单件体积 (立方英尺)", min_value=0.001, value=0.185, step=0.01, format="%.4f")

size_category = st.sidebar.selectbox(
    "尺寸分类",
    ["标准尺寸", "大件及超大件"]
)

special_category = st.sidebar.checkbox(
    "属于特殊类目 (服装、鞋靴、箱包、珠宝首饰和钟表)",
    value=False,
    help="特殊类目在 181~270 天库龄段享有较低的费率"
)

st.sidebar.header("📊 2. 库存与销售")
current_inventory = st.sidebar.number_input("当前库存数量 (件)", min_value=0, value=1000, step=10)
daily_sales = st.sidebar.number_input("预计日均销量 (件/天)", min_value=0.0, value=10.0, step=1.0)
current_age_days = st.sidebar.number_input("当前平均库龄 (天)", min_value=0, value=30, step=1)

start_month = st.sidebar.selectbox(
    "预测起始月份",
    options=list(range(1, 13)),
    index=datetime.now().month - 1,
    format_func=lambda x: f"{x} 月"
)

# ================= 费率表定义 =================
# 结构: (库龄下限, 库龄上限, 标准费率, 大件费率, 特殊标准费率, 特殊大件费率)
# 注意：366天以上是 "max(每立方英尺费率, 基础费率+每件附加费)" 的双轨制
TIER_RULES = [
    {"min": 0,   "max": 180, "std": 0.78, "ovs": 0.56, "sp_std": 0.78, "sp_ovs": 0.56},
    {"min": 181, "max": 210, "std": 2.78, "ovs": 2.56, "sp_std": 1.78, "sp_ovs": 1.56},
    {"min": 211, "max": 240, "std": 2.78, "ovs": 2.56, "sp_std": 1.78, "sp_ovs": 1.56},
    {"min": 241, "max": 270, "std": 2.78, "ovs": 2.56, "sp_std": 1.78, "sp_ovs": 1.56},
    {"min": 271, "max": 300, "std": 6.68, "ovs": 6.46, "sp_std": 6.68, "sp_ovs": 6.46},
    {"min": 301, "max": 330, "std": 6.68, "ovs": 6.46, "sp_std": 6.68, "sp_ovs": 6.46},
    {"min": 331, "max": 365, "std": 6.68, "ovs": 6.46, "sp_std": 6.68, "sp_ovs": 6.46},
    # 366~455 天：双轨制
    {"min": 366, "max": 455, "std_flat": 7.68, "ovs_flat": 7.46, "sp_std_flat": 7.68, "sp_ovs_flat": 7.46,
     "std_base": 0.78, "ovs_base": 0.56, "sp_std_base": 0.78, "sp_ovs_base": 0.56, "per_unit": 0.30},
    # 456 天以上：双轨制
    {"min": 456, "max": 99999, "std_flat": 8.68, "ovs_flat": 8.46, "sp_std_flat": 8.68, "sp_ovs_flat": 8.46,
     "std_base": 0.78, "ovs_base": 0.56, "sp_std_base": 0.78, "sp_ovs_base": 0.56, "per_unit": 0.35},
]

def get_storage_fee(volume, units, age_days, is_peak, size_cat, is_special):
    """返回当月仓储费 (USD) 和匹配的费率描述"""
    tier = None
    for t in TIER_RULES:
        if t["min"] <= age_days <= t["max"]:
            tier = t
            break
    if tier is None:
        return 0.0, "库龄异常"

    # 确定使用哪套费率键
    if size_cat == "标准尺寸":
        key_flat = "sp_std_flat" if is_special else "std_flat"
        key_base = "sp_std_base" if is_special else "std_base"
        key_std = "sp_std" if is_special else "std"
        cat_label = "特殊标准" if is_special else "标准"
    else:
        key_flat = "sp_ovs_flat" if is_special else "ovs_flat"
        key_base = "sp_ovs_base" if is_special else "ovs_base"
        key_std = "sp_ovs" if is_special else "ovs"
        cat_label = "特殊大件" if is_special else "大件"

    if "flat" in tier:  # 366天以上的双轨制
        fee_flat = volume * tier[key_flat]
        fee_per_unit = volume * tier[key_base] + units * tier["per_unit"]
        base_fee = max(fee_flat, fee_per_unit)
        rate_desc = f"max({tier[key_flat]}/cu.ft., {tier[key_base]}+${tier['per_unit']}/件)"
    else:
        base_fee = volume * tier[key_std]
        rate_desc = f"${tier[key_std]}/cu.ft."

    # 旺季附加
    peak_surcharge = 0.0
    if is_peak:
        peak_rate = 1.62 if size_cat == "标准尺寸" else 0.84
        peak_surcharge = volume * peak_rate
        rate_desc += f" + 旺季${peak_rate}"

    total_fee = base_fee + peak_surcharge
    return total_fee, f"{cat_label} | {rate_desc}"

# ================= 模拟计算 =================
if st.sidebar.button("🚀 生成未来 12 个月预测", type="primary"):
    if daily_sales <= 0:
        st.error("日均销量必须大于 0，否则库存永远不会消耗，无法模拟库龄增长。")
        st.stop()

    results = []
    inv = current_inventory
    age = current_age_days
    month = start_month
    
    for i in range(12):
        # 判断当月是否旺季 (10,11,12月)
        is_peak = month in [10, 11, 12]
        
        # 月均库存 = (期初 + 期末) / 2
        end_inv = max(0, inv - daily_sales * 30)
        avg_inv = (inv + end_inv) / 2
        
        if avg_inv <= 0:
            # 库存已耗尽，后续月份全为 0
            fee_usd, fee_desc = 0.0, "无库存"
            total_volume = 0.0
        else:
            total_volume = avg_inv * unit_volume
            fee_usd, fee_desc = get_storage_fee(
                total_volume, avg_inv, age, is_peak, size_category, special_category
            )
        
        results.append({
            "月份": f"{month}月",
            "是否旺季": "🔥 旺季" if is_peak else "淡季",
            "期初库存": int(inv),
            "月均库存": int(avg_inv),
            "库龄 (天)": int(age),
            "计费体积 (cu.ft.)": round(total_volume, 2),
            "费率档位与规则": fee_desc,
            "仓储费 (USD)": round(fee_usd, 2),
            "仓储费 (CNY)": round(fee_usd * usd_cny, 2),
        })
        
        # 更新下个月的期初和库龄
        inv = end_inv
        age += 30  # 简化按每月30天推进
        month = month % 12 + 1  # 月份循环

    df_result = pd.DataFrame(results)
    
    # ================= 展示结果 =================
    st.subheader("📅 未来 12 个月仓储费预测明细")
    
    col1, col2, col3 = st.columns(3)
    total_usd = df_result["仓储费 (USD)"].sum()
    col1.metric("12个月总仓储费 (USD)", f"${total_usd:,.2f}")
    col2.metric("12个月总仓储费 (CNY)", f"¥{total_usd * usd_cny:,.2f}")
    col3.metric("单件体积", f"{unit_volume:.4f} cu.ft.")
    
    # 格式化显示 DataFrame
    st.dataframe(
        df_result,
        use_container_width=True,
        hide_index=True,
        column_config={
            "仓储费 (USD)": st.column_config.NumberColumn(format="$%.2f"),
            "仓储费 (CNY)": st.column_config.NumberColumn(format="¥%.2f"),
        }
    )
    
    # 费用趋势图
    st.subheader("📈 月度费用趋势")
    chart_df = df_result.set_index("月份")[["仓储费 (USD)", "仓储费 (CNY)"]]
    st.line_chart(chart_df)
    
    st.caption(f"💡 汇率来源：open.er-api.com | 当前 1 USD = {usd_cny:.4f} CNY | 库龄每月按 30 天递增 | 月均库存 = (期初+期末)/2")

else:
    st.info("👈 请在左侧填写产品尺寸、库存和销售参数，然后点击 **「生成未来 12 个月预测」**。")
