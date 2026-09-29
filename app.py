import contextlib
import html
import json
import os
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path

import streamlit as st

DATA_FILE = Path(__file__).parent / "shopping_list.json"
MAX_NAME_LEN = 50
MIN_QTY, MAX_QTY = 1, 999


# ---------- 데이터 모델 ----------
def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def new_item(name: str, quantity: int = 1) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "name": name,
        "quantity": int(quantity),
        "checked": False,
        "created_at": now_iso(),
    }


def clean_name(name: str) -> str:
    return name.strip()[:MAX_NAME_LEN]


def normalize_item(raw, seen_ids: set[str]) -> dict | None:
    """파일에서 읽은 아이템 하나를 데이터 모델(PRD 5장)에 맞게 고친다. 살릴 수 없으면 None."""
    if not isinstance(raw, dict) or not isinstance(raw.get("name"), str):
        return None
    name = clean_name(raw["name"])
    if not name:
        return None
    try:
        quantity = int(raw.get("quantity", 1))
    except (TypeError, ValueError, OverflowError):
        quantity = 1
    item_id = raw.get("id")
    if not isinstance(item_id, str) or not item_id or item_id in seen_ids:
        item_id = str(uuid.uuid4())
    seen_ids.add(item_id)
    created_at = raw.get("created_at")
    return {
        "id": item_id,
        "name": name,
        "quantity": min(max(quantity, MIN_QTY), MAX_QTY),
        "checked": raw.get("checked") is True,
        "created_at": created_at if isinstance(created_at, str) else now_iso(),
    }


# ---------- 저장소 ----------
def write_items(items: list[dict]) -> None:
    """임시 파일에 다 쓴 뒤 교체한다. 도중에 끊겨도 기존 파일은 온전히 남는다."""
    tmp = DATA_FILE.with_name(DATA_FILE.name + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, DATA_FILE)
    except OSError:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise


def load_items() -> tuple[list[dict], str | None, bool]:
    """(목록, 사용자 알림, 저장 가능 여부)를 돌려준다. 파일이 어떤 상태든 앱은 멈추지 않는다.

    버려야 하는 내용이 있으면 원본을 백업한 뒤 정리본으로 다시 쓴다.
    읽거나 백업할 수 없는 파일은 데이터를 지키기 위해 이번 실행에서 덮어쓰지 않는다.
    """
    if not DATA_FILE.exists():
        return [], None, True
    try:
        text = DATA_FILE.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = None
    except OSError as e:
        return [], f"저장 파일을 읽을 수 없어 이번 실행에서는 저장하지 않습니다. ({e.strerror or e})", False
    if text is not None and not text.strip():
        return [], None, True

    try:
        data = json.loads(text) if text is not None else None
    except json.JSONDecodeError:
        data = None
    raw_items = data if isinstance(data, list) else None

    seen_ids: set[str] = set()
    items = [it for raw in raw_items or [] if (it := normalize_item(raw, seen_ids))]
    if raw_items is not None and len(items) == len(raw_items):
        return items, None, True  # 고쳐진 값(수량 범위 등)은 다음 저장 때 반영된다

    backup = DATA_FILE.with_name(f"{DATA_FILE.stem}.backup-{datetime.now():%Y%m%d-%H%M%S}.json")
    try:
        shutil.copy2(DATA_FILE, backup)
        write_items(items)
    except OSError as e:
        return items, f"저장 파일에 문제가 있지만 백업하지 못해 이번 실행에서는 저장하지 않습니다. ({e.strerror or e})", False
    if raw_items is None:
        return [], f"저장 파일이 손상돼 빈 목록으로 시작합니다. 원본은 {backup.name}에 보관했습니다.", True
    dropped = len(raw_items) - len(items)
    return items, f"형식이 잘못된 항목 {dropped}개를 제외하고 불러왔습니다. 원본은 {backup.name}에 보관했습니다.", True


def save_items() -> None:
    """현재 목록을 저장한다. 실패해도 앱을 멈추지 않고 화면 위쪽에 알린다."""
    if not st.session_state.can_save:
        return  # 이유는 load_notice로 이미 표시 중
    try:
        write_items(st.session_state.shopping_items)
    except OSError as e:
        st.session_state.save_error = (
            f"저장하지 못했습니다 ({e.strerror or e}). "
            "변경 내용은 화면에만 남아 있고, 다음 변경 때 다시 저장을 시도합니다."
        )
    else:
        st.session_state.save_error = None


# ---------- 상태 초기화 ----------
# 주의: 키 이름으로 "items"를 쓰면 session_state의 .items() 메서드와 충돌한다.
if "shopping_items" not in st.session_state:
    (
        st.session_state.shopping_items,
        st.session_state.load_notice,
        st.session_state.can_save,
    ) = load_items()
    st.session_state.save_error = None
if "editing_id" not in st.session_state:
    st.session_state.editing_id = None


def find_item(item_id: str) -> dict | None:
    return next((i for i in st.session_state.shopping_items if i["id"] == item_id), None)


# ---------- 콜백 ----------
def add_item() -> None:
    name = clean_name(st.session_state.new_name)
    if not name:
        st.session_state.add_error = "품목명을 입력해 주세요."
        return
    st.session_state.shopping_items.append(new_item(name, st.session_state.new_qty))
    save_items()


def toggle_item(item_id: str) -> None:
    item = find_item(item_id)
    if item:
        item["checked"] = st.session_state[f"chk_{item_id}"]
        save_items()


def start_edit(item_id: str) -> None:
    st.session_state.editing_id = item_id


def cancel_edit() -> None:
    st.session_state.editing_id = None


def save_edit(item_id: str) -> None:
    item = find_item(item_id)
    if not item:
        st.session_state.editing_id = None
        return
    name = clean_name(st.session_state[f"edit_name_{item_id}"])
    if not name:
        st.session_state.edit_error = "품목명을 입력해 주세요."
        return
    item["name"] = name
    item["quantity"] = int(st.session_state[f"edit_qty_{item_id}"])
    st.session_state.editing_id = None
    save_items()


def delete_item(item_id: str) -> None:
    st.session_state.shopping_items = [
        i for i in st.session_state.shopping_items if i["id"] != item_id
    ]
    if st.session_state.editing_id == item_id:
        st.session_state.editing_id = None
    save_items()


def clear_checked() -> None:
    st.session_state.shopping_items = [
        i for i in st.session_state.shopping_items if not i["checked"]
    ]
    if find_item(st.session_state.editing_id or "") is None:
        st.session_state.editing_id = None
    save_items()


# ---------- 표시용 헬퍼 ----------
_MD_SPECIAL = re.compile(r"[\\`*_{}\[\]()#+\-.!|~$]")


def escape_label(text: str) -> str:
    """사용자 입력을 st.markdown(unsafe_allow_html=True)에 안전하게 넣는다.

    마크다운 특수문자를 먼저 백슬래시로 이스케이프한 뒤 HTML(&, <, >)을 이스케이프한다.
    순서가 바뀌면 &lt; 같은 엔티티가 깨진다.
    """
    return html.escape(_MD_SPECIAL.sub(lambda m: "\\" + m.group(), text), quote=False)


CSS = """
<style>
/* 좁은 화면에서도 행의 컬럼이 세로로 쌓이지 않게 한다 */
[data-testid="stHorizontalBlock"] { flex-wrap: nowrap !important; gap: 0.5rem; }
[data-testid="stColumn"] { min-width: 0 !important; }
/* 체크박스·버튼 컬럼은 내용 폭만 차지하고, 나머지 폭은 이름/입력 컬럼이 쓴다 */
[data-testid="stColumn"]:has([data-testid="stCheckbox"]),
[data-testid="stColumn"]:has([data-testid="stButton"]),
[data-testid="stColumn"]:has([data-testid="stFormSubmitButton"]) {
    flex: 0 0 auto !important; width: auto !important;
}
[data-testid="stButton"] p, [data-testid="stFormSubmitButton"] p { white-space: nowrap; }
.item-label { word-break: break-all; }
.item-label.done { color: gray; text-decoration: line-through; }
</style>
"""

# ---------- UI ----------
st.set_page_config(page_title="쇼핑리스트", page_icon="🛒")
st.markdown(CSS, unsafe_allow_html=True)
st.title("🛒 쇼핑리스트")

if st.session_state.load_notice:
    st.warning(st.session_state.load_notice)
if st.session_state.save_error:
    st.error(st.session_state.save_error)

items = st.session_state.shopping_items
total = len(items)
done = sum(1 for i in items if i["checked"])

# 진행 현황 (F5)
st.caption(f"구매 완료 {done} / {total}")
st.progress(done / total if total else 0.0)
if total and done == total:
    st.success("🎉 모두 구매했어요!")

# 추가 폼 (F1)
with st.form("add_form", clear_on_submit=True, border=False):
    c1, c2, c3 = st.columns([5, 2, 1], vertical_alignment="bottom")
    c1.text_input("품목명", max_chars=MAX_NAME_LEN, placeholder="예: 우유", key="new_name")
    c2.number_input("수량", min_value=MIN_QTY, max_value=MAX_QTY, value=1, step=1, key="new_qty")
    c3.form_submit_button("추가", type="primary", on_click=add_item)

if err := st.session_state.pop("add_error", None):
    st.warning(err)

st.divider()

# 목록 (F2~F4)
if not items:
    st.info("목록이 비어 있습니다. 살 물건을 추가해 보세요!")

for item in items:
    item_id = item["id"]

    if st.session_state.editing_id == item_id:
        c1, c2, c3, c4 = st.columns([5, 2, 1, 1], vertical_alignment="center")
        c1.text_input(
            "품목명 수정", value=item["name"], max_chars=MAX_NAME_LEN,
            key=f"edit_name_{item_id}", label_visibility="collapsed",
        )
        c2.number_input(
            "수량 수정", min_value=MIN_QTY, max_value=MAX_QTY, value=item["quantity"], step=1,
            key=f"edit_qty_{item_id}", label_visibility="collapsed",
        )
        c3.button("저장", key=f"save_{item_id}", type="primary", on_click=save_edit, args=(item_id,))
        c4.button("취소", key=f"cancel_{item_id}", on_click=cancel_edit)
        if err := st.session_state.pop("edit_error", None):
            st.warning(err)
    else:
        c1, c2, c3, c4 = st.columns([1, 8, 1, 1], vertical_alignment="center")
        c1.checkbox(
            "구매 완료", value=item["checked"], key=f"chk_{item_id}",
            on_change=toggle_item, args=(item_id,), label_visibility="collapsed",
        )
        css_class = "item-label done" if item["checked"] else "item-label"
        c2.markdown(
            f"<span class='{css_class}'>{escape_label(item['name'])} × {item['quantity']}</span>",
            unsafe_allow_html=True,
        )
        c3.button("수정", key=f"edit_{item_id}", on_click=start_edit, args=(item_id,))
        c4.button("삭제", key=f"del_{item_id}", on_click=delete_item, args=(item_id,))

# 일괄 삭제 (F4)
if done:
    st.divider()
    st.button("체크된 항목 모두 삭제", on_click=clear_checked)
