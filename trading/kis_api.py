"""KIS REST API 직접 호출 클라이언트

MCP를 거치지 않고 KIS API를 직접 호출하여
분봉, 거래량순위, 등락률순위, 해외주식 주문/조회 데이터를 조회한다.
"""
import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from core.config import settings
from trading.market_profile import (
    kis_balance_exchange_code,
    kis_exchange_code,
    kis_order_exchange_code,
    market_currency,
    normalize_market,
)

DOMAIN = "https://openapi.koreainvestment.com:9443"
VIRTUAL_DOMAIN = "https://openapivts.koreainvestment.com:29443"
TOKEN_FILE = Path("data/kis_token.json")

# 토큰 동시 발급 방지용 Lock + 메모리 캐시
_token_lock = asyncio.Lock()
_cached_token: str | None = None
_cached_expires_at: datetime | None = None


def _get_domain() -> str:
    """계좌 유형에 따른 도메인 반환 (조회 API는 실전 도메인 공통)"""
    return DOMAIN


def _get_trading_domain() -> str:
    """주문/계좌 API용 도메인 반환"""
    if settings.KIS_ACCOUNT_TYPE.upper() == "VIRTUAL":
        return VIRTUAL_DOMAIN
    return DOMAIN


def _get_app_key() -> str:
    if settings.KIS_ACCOUNT_TYPE.upper() == "VIRTUAL":
        return settings.KIS_PAPER_APP_KEY or settings.KIS_APP_KEY
    return settings.KIS_APP_KEY


def _get_app_secret() -> str:
    if settings.KIS_ACCOUNT_TYPE.upper() == "VIRTUAL":
        return settings.KIS_PAPER_APP_SECRET or settings.KIS_APP_SECRET
    return settings.KIS_APP_SECRET


def _resolve_account_parts(raw: str, prod_type_override: str = "") -> tuple[str, str]:
    """KIS 계좌번호를 CANO/상품코드로 정규화"""
    digits = "".join(ch for ch in raw if ch.isdigit())
    prod_digits = "".join(ch for ch in prod_type_override if ch.isdigit())

    if prod_digits and len(prod_digits) != 2:
        raise ValueError("KIS_PROD_TYPE 는 2자리여야 합니다.")

    if len(digits) == 10:
        return digits[:8], prod_digits or digits[8:10]

    if len(digits) == 8 and prod_digits:
        return digits, prod_digits

    raise ValueError(
        "KIS 계좌번호는 10자리 전체 또는 CANO 8자리 + KIS_PROD_TYPE 이 필요합니다."
    )


def _get_account_parts() -> tuple[str, str]:
    """활성 계좌번호를 CANO/상품코드로 분리"""
    raw = settings.KIS_PAPER_STOCK if settings.is_paper_trading else settings.KIS_ACCT_STOCK
    return _resolve_account_parts(raw, settings.KIS_PROD_TYPE)


def _request_headers(token: str, tr_id: str, tr_cont: str = "") -> dict[str, str]:
    """KIS REST 공통 헤더"""
    headers = {
        "content-type": "application/json",
        "authorization": f"Bearer {token}",
        "appkey": _get_app_key(),
        "appsecret": _get_app_secret(),
        "tr_id": tr_id,
    }
    if tr_cont:
        headers["tr_cont"] = tr_cont
    return headers


async def _request_json(
    api_url: str,
    tr_id: str,
    params: dict[str, Any] | None = None,
    method: str = "GET",
    use_trading_domain: bool = False,
    tr_cont: str = "",
) -> dict[str, Any]:
    """KIS REST API 호출 후 JSON 응답 반환"""
    domain = _get_trading_domain() if use_trading_domain else _get_domain()
    async with httpx.AsyncClient(timeout=20.0) as client:
        token = await _get_access_token(client)
        headers = _request_headers(token, tr_id, tr_cont=tr_cont)

        if method.upper() == "POST":
            response = await client.post(f"{domain}{api_url}", headers=headers, json=params or {})
        else:
            response = await client.get(f"{domain}{api_url}", headers=headers, params=params or {})

    try:
        data = response.json()
    except json.JSONDecodeError:
        data = {"rt_cd": "1", "msg1": response.text}

    if not isinstance(data, dict):
        data = {"output": data}

    data.setdefault("http_status", response.status_code)
    data.setdefault("tr_cont", response.headers.get("tr_cont", ""))
    return data


def _append_records(target: list[dict[str, Any]], value: Any) -> None:
    """KIS output 레코드를 list 형태로 누적"""
    if isinstance(value, list):
        target.extend(item for item in value if isinstance(item, dict))
        return
    if isinstance(value, dict):
        target.append(value)


async def _request_paged_json(
    api_url: str,
    tr_id: str,
    params: dict[str, Any],
    output_keys: tuple[str, ...],
    ctx_fk_key: str = "CTX_AREA_FK200",
    ctx_nk_key: str = "CTX_AREA_NK200",
) -> dict[str, Any]:
    """연속조회형 KIS API를 단일 dict로 병합"""
    merged: dict[str, Any] = {}
    accumulators = {key: [] for key in output_keys}
    tr_cont = ""
    next_fk = params.get(ctx_fk_key, "")
    next_nk = params.get(ctx_nk_key, "")

    for index in range(10):
        page_params = {**params, ctx_fk_key: next_fk, ctx_nk_key: next_nk}
        page = await _request_json(
            api_url,
            tr_id,
            params=page_params,
            method="GET",
            use_trading_domain=True,
            tr_cont=tr_cont,
        )
        merged = page
        for key in output_keys:
            _append_records(accumulators[key], page.get(key))

        header_tr_cont = ""
        header = page.get("header")
        if isinstance(header, dict):
            header_tr_cont = str(header.get("tr_cont", ""))
        if not header_tr_cont:
            header_tr_cont = str(page.get("tr_cont", ""))

        next_fk = str(page.get(ctx_fk_key.lower(), ""))
        next_nk = str(page.get(ctx_nk_key.lower(), ""))
        if header_tr_cont not in {"M", "F"} or (not next_fk and not next_nk) or index >= 9:
            break
        tr_cont = "N"

    for key, records in accumulators.items():
        merged[key] = records
    return merged


async def _get_access_token(client: httpx.AsyncClient) -> str:
    """토큰 발급 (메모리 캐시 + Lock으로 동시 발급 방지)

    asyncio.gather()로 병렬 호출 시 Lock으로 직렬화하여
    KIS 1분당 1회 토큰 발급 제한(EGW00133) 에러를 방지한다.
    EGW00133 시 Lock을 해제한 뒤 60초 대기 → 재시도 (Lock 점유 최소화).
    """
    global _cached_token, _cached_expires_at

    # 빠른 경로: 메모리 캐시에 유효한 토큰이 있으면 즉시 반환 (Lock 불필요)
    if _cached_token and _cached_expires_at and datetime.now() < _cached_expires_at:
        return _cached_token

    for attempt in range(2):
        async with _token_lock:
            # Double-check: 다른 태스크가 Lock 대기 중 이미 발급했을 수 있음
            if _cached_token and _cached_expires_at and datetime.now() < _cached_expires_at:
                return _cached_token

            # 파일 캐시 확인
            if TOKEN_FILE.exists():
                try:
                    token_data = json.loads(TOKEN_FILE.read_text())
                    expires_at = datetime.fromisoformat(token_data["expires_at"])
                    if datetime.now() < expires_at:
                        _cached_token = token_data["token"]
                        _cached_expires_at = expires_at
                        logger.debug("KIS 토큰: 파일 캐시에서 로드")
                        return _cached_token
                except Exception:
                    pass

            # 새 토큰 발급
            token = await _issue_new_token(client)
            if token is not None:
                return token

        # EGW00133 발급 제한 → Lock 해제 후 대기 (다른 API 호출 차단 안 함)
        if attempt == 0:
            logger.warning("KIS 토큰 발급 제한(EGW00133), 60초 대기 후 재시도")
            await asyncio.sleep(60)

    raise Exception("KIS 토큰 발급 실패: 재시도 횟수 초과")


async def _issue_new_token(client: httpx.AsyncClient) -> str | None:
    """실제 토큰 발급 요청 (_token_lock 내부에서 호출)

    성공 시 토큰 반환, EGW00133 시 None 반환 (호출자가 Lock 해제 후 재시도).
    """
    global _cached_token, _cached_expires_at

    resp = await client.post(
        f"{DOMAIN}/oauth2/tokenP",
        headers={"content-type": "application/json"},
        json={
            "grant_type": "client_credentials",
            "appkey": _get_app_key(),
            "appsecret": _get_app_secret(),
        },
    )

    if resp.status_code != 200:
        resp_text = resp.text
        if "EGW00133" in resp_text:
            return None  # 호출자가 Lock 해제 후 대기·재시도
        raise Exception(f"KIS 토큰 발급 실패: {resp_text}")

    data = resp.json()
    token = data["access_token"]
    expires_at = datetime.now() + timedelta(hours=23)

    # 메모리 캐시 갱신
    _cached_token = token
    _cached_expires_at = expires_at

    # 파일 캐시 저장
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps({
        "token": token,
        "expires_at": expires_at.isoformat(),
    }))

    logger.info("KIS 토큰 신규 발급 완료")
    return token


async def get_minute_chart(symbol: str, period: str = "5") -> dict:
    """국내 주식 분봉 차트 조회

    Args:
        symbol: 종목코드 (예: 005930)
        period: 분봉 간격 - "1", "5", "15", "30", "60"

    Returns:
        {"success": True, "prices": [{"time", "open", "high", "low", "close", "volume"}, ...]}
    """
    end_time = datetime.now().strftime("%H%M%S")

    try:
        async with httpx.AsyncClient() as client:
            token = await _get_access_token(client)
            response = await client.get(
                f"{DOMAIN}/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice",
                headers={
                    "content-type": "application/json",
                    "authorization": f"Bearer {token}",
                    "appkey": _get_app_key(),
                    "appsecret": _get_app_secret(),
                    "tr_id": "FHKST03010200",
                },
                params={
                    "FID_ETC_CLS_CODE": "",
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_INPUT_ISCD": symbol,
                    "FID_INPUT_HOUR_1": end_time,
                    "FID_PW_DATA_INCU_YN": "N",
                },
            )

            if response.status_code != 200:
                logger.warning("분봉 조회 실패: HTTP {}", response.status_code)
                return {"success": False, "error": f"HTTP {response.status_code}", "prices": []}

            result = response.json()

        if not result or "output2" not in result:
            return {"success": False, "error": "분봉 데이터 없음", "prices": []}

        prices = []
        for item in result.get("output2", []):
            prices.append({
                "time": item.get("stck_cntg_hour", ""),
                "open": item.get("stck_oprc", "0"),
                "high": item.get("stck_hgpr", "0"),
                "low": item.get("stck_lwpr", "0"),
                "close": item.get("stck_prpr", "0"),
                "volume": item.get("cntg_vol", "0"),
            })

        return {"success": True, "symbol": symbol, "period": period, "prices": prices}
    except Exception as e:
        logger.error("분봉 조회 오류 ({}): {}", symbol, str(e))
        return {"success": False, "error": str(e), "prices": []}


async def get_volume_rank(market: str = "J") -> dict:
    """거래량순위 조회 (KIS REST API 직접 호출)

    Args:
        market: 시장 구분 - "J"(KRX), "NX"(NXT), "UN"(통합)

    Returns:
        {"success": True, "stocks": [{"symbol", "name", "price", "change", "change_rate", "volume", ...}, ...]}
    """
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            token = await _get_access_token(client)
            response = await client.get(
                f"{DOMAIN}/uapi/domestic-stock/v1/quotations/volume-rank",
                headers={
                    "content-type": "application/json",
                    "authorization": f"Bearer {token}",
                    "appkey": _get_app_key(),
                    "appsecret": _get_app_secret(),
                    "tr_id": "FHPST01710000",
                },
                params={
                    "FID_COND_MRKT_DIV_CODE": market,
                    "FID_COND_SCR_DIV_CODE": "20171",
                    "FID_INPUT_ISCD": "0000",           # 전체 종목
                    "FID_DIV_CLS_CODE": "0",             # 전체 (보통주+우선주)
                    "FID_BLNG_CLS_CODE": "0",            # 평균거래량 기준
                    "FID_TRGT_CLS_CODE": "111111111",    # 전체 대상
                    "FID_TRGT_EXLS_CLS_CODE": "0000000110",  # 관리종목·감리종목 제외
                    "FID_INPUT_PRICE_1": "",              # 가격 필터 없음
                    "FID_INPUT_PRICE_2": "",
                    "FID_VOL_CNT": "",                   # 거래량 필터 없음
                    "FID_INPUT_DATE_1": "",
                },
            )

            if response.status_code != 200:
                logger.warning("거래량순위 조회 실패: HTTP {}", response.status_code)
                return {"success": False, "error": f"HTTP {response.status_code}", "stocks": []}

            result = response.json()

        output = result.get("output", [])
        if not output:
            return {"success": False, "error": "거래량순위 데이터 없음", "stocks": []}

        stocks = []
        for item in output[:30]:  # 최대 30개
            stocks.append({
                "symbol": item.get("mksc_shrn_iscd", item.get("stck_shrn_iscd", "")),
                "name": item.get("hts_kor_isnm", ""),
                "price": item.get("stck_prpr", "0"),
                "current_price": item.get("stck_prpr", "0"),
                "change": item.get("prdy_vrss", "0"),
                "change_rate": item.get("prdy_ctrt", "0"),
                "volume": item.get("acml_vol", "0"),
                "trade_amount": item.get("acml_tr_pbmn", "0"),
                "prev_volume": item.get("prdy_vol", "0"),
                "volume_increase_rate": item.get("vol_inrt", "0"),
                "change_sign": item.get("prdy_vrss_sign", ""),
            })

        logger.info("거래량순위 조회 완료: {}건", len(stocks))
        return {"success": True, "stocks": stocks}
    except Exception as e:
        logger.error("거래량순위 조회 오류: {}", str(e))
        return {"success": False, "error": str(e), "stocks": []}


async def get_fluctuation_rank(sort: str = "top", market: str = "J") -> dict:
    """등락률순위 조회 (KIS REST API 직접 호출)

    Args:
        sort: "top"(급등 상위) 또는 "bottom"(급락 하위)
        market: 시장 구분 - "J"(KRX), "NX"(NXT)

    Returns:
        {"success": True, "stocks": [{"symbol", "name", "price", "change", "change_rate", "volume", ...}, ...]}
    """
    # sort에 따라 정렬 코드 설정
    # 0: 상승률순, 1: 하락률순 (KIS API 기준)
    rank_sort = "0" if sort == "top" else "1"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            token = await _get_access_token(client)
            response = await client.get(
                f"{DOMAIN}/uapi/domestic-stock/v1/ranking/fluctuation",
                headers={
                    "content-type": "application/json",
                    "authorization": f"Bearer {token}",
                    "appkey": _get_app_key(),
                    "appsecret": _get_app_secret(),
                    "tr_id": "FHPST01700000",
                },
                params={
                    "fid_cond_mrkt_div_code": market,
                    "fid_cond_scr_div_code": "20170",
                    "fid_input_iscd": "0000",             # 전체 종목
                    "fid_rank_sort_cls_code": rank_sort,
                    "fid_input_cnt_1": "0",               # 조회 종목 수 (0=기본값 30)
                    "fid_prc_cls_code": "0",              # 전체 가격대
                    "fid_input_price_1": "",               # 가격 필터 없음
                    "fid_input_price_2": "",
                    "fid_vol_cnt": "",                    # 거래량 필터 없음
                    "fid_trgt_cls_code": "111111111",     # 전체 대상
                    "fid_trgt_exls_cls_code": "0000000110",  # 관리종목·감리종목 제외
                    "fid_div_cls_code": "0",              # 전체 (보통주+우선주)
                    "fid_rsfl_rate1": "",                  # 등락률 필터 없음
                    "fid_rsfl_rate2": "",
                },
            )

            if response.status_code != 200:
                logger.warning("등락률순위 조회 실패: HTTP {}", response.status_code)
                return {"success": False, "error": f"HTTP {response.status_code}", "stocks": []}

            result = response.json()

        output = result.get("output", [])
        if not output:
            return {"success": False, "error": "등락률순위 데이터 없음", "stocks": []}

        stocks = []
        for item in output[:30]:  # 최대 30개
            stocks.append({
                "symbol": item.get("mksc_shrn_iscd", item.get("stck_shrn_iscd", "")),
                "name": item.get("hts_kor_isnm", ""),
                "price": item.get("stck_prpr", "0"),
                "current_price": item.get("stck_prpr", "0"),
                "change": item.get("prdy_vrss", "0"),
                "change_rate": item.get("prdy_ctrt", "0"),
                "volume": item.get("acml_vol", "0"),
                "trade_amount": item.get("acml_tr_pbmn", "0"),
                "change_sign": item.get("prdy_vrss_sign", ""),
            })

        logger.info("등락률순위({}) 조회 완료: {}건", sort, len(stocks))
        return {"success": True, "stocks": stocks}
    except Exception as e:
        logger.error("등락률순위 조회 오류: {}", str(e))
        return {"success": False, "error": str(e), "stocks": []}


async def get_overseas_price(symbol: str, market: str = "NASDAQ") -> dict:
    """해외주식 현재가 조회"""
    market_code = normalize_market(market)
    try:
        result = await _request_json(
            "/uapi/overseas-price/v1/quotations/price",
            "HHDFS00000300",
            params={
                "AUTH": "",
                "EXCD": kis_exchange_code(market_code),
                "SYMB": symbol,
            },
        )
        result["success"] = result.get("rt_cd") == "0"
        result["market"] = market_code
        result["currency"] = market_currency(market_code)
        return result
    except Exception as e:
        logger.error("해외 현재가 조회 오류 ({} {}): {}", market_code, symbol, str(e))
        return {"success": False, "error": str(e), "output": {}}


async def get_overseas_daily_price(symbol: str, market: str = "NASDAQ") -> dict:
    """해외주식 기간별 시세 조회"""
    market_code = normalize_market(market)
    try:
        result = await _request_json(
            "/uapi/overseas-price/v1/quotations/dailyprice",
            "HHDFS76240000",
            params={
                "AUTH": "",
                "EXCD": kis_exchange_code(market_code),
                "SYMB": symbol,
                "GUBN": "0",
                "BYMD": "",
                "MODP": "0",
            },
        )
        result["success"] = result.get("rt_cd") == "0"
        result["market"] = market_code
        result["currency"] = market_currency(market_code)
        return result
    except Exception as e:
        logger.error("해외 일봉 조회 오류 ({} {}): {}", market_code, symbol, str(e))
        return {"success": False, "error": str(e), "output1": [], "output2": []}


async def get_overseas_minute_chart(
    symbol: str,
    market: str = "NASDAQ",
    period: str = "5",
) -> dict:
    """해외주식 분봉 조회"""
    market_code = normalize_market(market)
    try:
        result = await _request_json(
            "/uapi/overseas-price/v1/quotations/inquire-time-itemchartprice",
            "HHDFS76950200",
            params={
                "AUTH": "",
                "EXCD": kis_exchange_code(market_code),
                "SYMB": symbol,
                "NMIN": period,
                "PINC": "1",
                "NEXT": "",
                "NREC": "120",
                "FILL": "",
                "KEYB": "",
            },
        )
        result["success"] = result.get("rt_cd") == "0"
        result["market"] = market_code
        result["currency"] = market_currency(market_code)
        return result
    except Exception as e:
        logger.error("해외 분봉 조회 오류 ({} {}): {}", market_code, symbol, str(e))
        return {"success": False, "error": str(e), "output1": [], "output2": []}


async def get_overseas_present_balance(market: str = "NASDAQ") -> dict:
    """해외주식 주문가능금액/평가금액 조회"""
    market_code = normalize_market(market)
    cano, acnt_prdt_cd = _get_account_parts()
    try:
        result = await _request_json(
            "/uapi/overseas-stock/v1/trading/inquire-present-balance",
            "VTRP6504R" if settings.is_paper_trading else "CTRP6504R",
            params={
                "CANO": cano,
                "ACNT_PRDT_CD": acnt_prdt_cd,
                "WCRC_FRCR_DVSN_CD": "01",
                "NATN_CD": "000",
                "TR_MKET_CD": "00",
                "INQR_DVSN_CD": "00",
            },
            use_trading_domain=True,
        )
        result["success"] = result.get("rt_cd") == "0"
        result["market"] = market_code
        result["currency"] = market_currency(market_code)
        return result
    except Exception as e:
        logger.error("해외 현재잔고 조회 오류 ({}): {}", market_code, str(e))
        return {"success": False, "error": str(e), "output1": [], "output2": []}


async def get_overseas_psamount(
    symbol: str,
    price: float,
    market: str = "NASDAQ",
) -> dict:
    """해외주식 종목별 매수가능금액 조회"""
    market_code = normalize_market(market)
    cano, acnt_prdt_cd = _get_account_parts()
    try:
        result = await _request_json(
            "/uapi/overseas-stock/v1/trading/inquire-psamount",
            "VTTS3007R" if settings.is_paper_trading else "TTTS3007R",
            params={
                "CANO": cano,
                "ACNT_PRDT_CD": acnt_prdt_cd,
                "OVRS_EXCG_CD": kis_order_exchange_code(market_code),
                "OVRS_ORD_UNPR": f"{float(price or 0.0):.8f}",
                "ITEM_CD": symbol,
            },
            use_trading_domain=True,
        )
        result["success"] = result.get("rt_cd") == "0"
        result["market"] = market_code
        result["currency"] = market_currency(market_code)
        return result
    except Exception as e:
        logger.error("해외 매수가능금액 조회 오류 ({} {}): {}", market_code, symbol, str(e))
        return {"success": False, "error": str(e), "output": {}}


async def get_overseas_balance(market: str = "NASDAQ") -> dict:
    """해외주식 보유잔고 조회"""
    market_code = normalize_market(market)
    cano, acnt_prdt_cd = _get_account_parts()
    try:
        result = await _request_paged_json(
            "/uapi/overseas-stock/v1/trading/inquire-balance",
            "VTTS3012R" if settings.is_paper_trading else "TTTS3012R",
            params={
                "CANO": cano,
                "ACNT_PRDT_CD": acnt_prdt_cd,
                "OVRS_EXCG_CD": kis_balance_exchange_code(market_code),
                "TR_CRCY_CD": market_currency(market_code),
                "CTX_AREA_FK200": "",
                "CTX_AREA_NK200": "",
            },
            output_keys=("output1", "output2"),
        )
        result["success"] = result.get("rt_cd") == "0"
        result["market"] = market_code
        result["currency"] = market_currency(market_code)
        return result
    except Exception as e:
        logger.error("해외 잔고 조회 오류 ({}): {}", market_code, str(e))
        return {"success": False, "error": str(e), "output1": [], "output2": []}


async def get_domestic_price(symbol: str) -> dict:
    """국내주식 현재가 조회"""
    try:
        result = await _request_json(
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            "FHKST01010100",
            params={
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": symbol,
            },
        )
        result["success"] = result.get("rt_cd") == "0"
        return result
    except Exception as e:
        logger.error("국내 현재가 조회 오류 ({}): {}", symbol, str(e))
        return {"success": False, "error": str(e), "output": {}}


async def get_domestic_balance() -> dict:
    """국내주식 잔고 조회 (연속조회 자동 처리)"""
    tr_id = "VTTC8434R" if settings.is_paper_trading else "TTTC8434R"
    cano, acnt_prdt_cd = _get_account_parts()
    try:
        result = await _request_paged_json(
            "/uapi/domestic-stock/v1/trading/inquire-balance",
            tr_id,
            params={
                "CANO": cano,
                "ACNT_PRDT_CD": acnt_prdt_cd,
                "AFHR_FLPR_YN": "N",
                "INQR_DVSN": "02",
                "UNPR_DVSN": "01",
                "FUND_STTL_ICLD_YN": "N",
                "FNCG_AMT_AUTO_RDPT_YN": "N",
                "PRCS_DVSN": "00",
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
            },
            output_keys=("output1", "output2"),
            ctx_fk_key="CTX_AREA_FK100",
            ctx_nk_key="CTX_AREA_NK100",
        )
        result["success"] = result.get("rt_cd") == "0"
        return result
    except Exception as e:
        logger.error("국내 잔고 조회 오류: {}", str(e))
        return {"success": False, "error": str(e), "output1": [], "output2": []}


async def get_domestic_daily_price(symbol: str) -> dict:
    """국내주식 일별 시세 조회"""
    try:
        result = await _request_json(
            "/uapi/domestic-stock/v1/quotations/inquire-daily-price",
            "FHKST01010400",
            params={
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": symbol,
                "FID_PERIOD_DIV_CODE": "D",
                "FID_ORG_ADJ_PRC": "1",
            },
        )
        result["success"] = result.get("rt_cd") == "0"
        return result
    except Exception as e:
        logger.error("국내 일별시세 조회 오류 ({}): {}", symbol, str(e))
        return {"success": False, "error": str(e), "output": []}


async def place_domestic_order(
    symbol: str,
    side: str,
    quantity: int,
    price: float | None = None,
) -> dict:
    """국내주식 주문 실행"""
    cano, acnt_prdt_cd = _get_account_parts()
    is_buy = str(side).upper() == "BUY"
    tr_id = (
        "VTTC0012U" if settings.is_paper_trading and is_buy
        else "VTTC0011U" if settings.is_paper_trading and not is_buy
        else "TTTC0012U" if is_buy
        else "TTTC0011U"
    )
    # 00=지정가, 01=시장가
    ord_dvsn = "00" if price else "01"
    ord_unpr = str(int(price)) if price else "0"
    try:
        result = await _request_json(
            "/uapi/domestic-stock/v1/trading/order-cash",
            tr_id,
            params={
                "CANO": cano,
                "ACNT_PRDT_CD": acnt_prdt_cd,
                "PDNO": symbol,
                "ORD_DVSN": ord_dvsn,
                "ORD_QTY": str(quantity),
                "ORD_UNPR": ord_unpr,
            },
            method="POST",
            use_trading_domain=True,
        )
        result["success"] = result.get("rt_cd") == "0"
        return result
    except Exception as e:
        logger.error("국내 주문 오류 ({} {}): {}", side, symbol, str(e))
        return {"success": False, "error": str(e), "output": {}}


async def get_domestic_asking_price(symbol: str) -> dict:
    """국내주식 호가 조회"""
    try:
        result = await _request_json(
            "/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn",
            "FHKST01010200",
            params={
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": symbol,
            },
        )
        result["success"] = result.get("rt_cd") == "0"
        return result
    except Exception as e:
        logger.error("국내 호가 조회 오류 ({}): {}", symbol, str(e))
        return {"success": False, "error": str(e), "output": {}}


async def get_domestic_order_list(start_date: str, end_date: str) -> dict:
    """국내주식 일별 체결 조회 (신 API: TTTC0081R/VTTC0081R)"""
    tr_id = "VTTC0081R" if settings.is_paper_trading else "TTTC0081R"
    cano, prod = _get_account_parts()
    try:
        result = await _request_json(
            "/uapi/domestic-stock/v1/trading/inquire-daily-ccld",
            tr_id=tr_id,
            params={
                "CANO": cano,
                "ACNT_PRDT_CD": prod,
                "INQR_STRT_DT": start_date,
                "INQR_END_DT": end_date,
                "SLL_BUY_DVSN_CD": "00",
                "INQR_DVSN": "01",
                "PDNO": "",
                "CCLD_DVSN": "00",
                "ORD_GNO_BRNO": "",
                "ODNO": "",
                "INQR_DVSN_3": "00",
                "INQR_DVSN_1": "",
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
            },
            use_trading_domain=True,
        )
        result["success"] = result.get("rt_cd") == "0"
        return result
    except Exception as e:
        logger.error("국내 주문내역 조회 오류: {}", str(e))
        return {"success": False, "error": str(e), "output1": [], "output2": {}}


async def get_overseas_order_list(market: str = "NASDAQ") -> dict:
    """해외주식 주문/체결 내역 조회"""
    market_code = normalize_market(market)
    cano, acnt_prdt_cd = _get_account_parts()
    from scheduler.market_calendar import market_calendar

    today = market_calendar.market_date(market=market_code).strftime("%Y%m%d")
    overseas_exchange = "" if settings.is_paper_trading else kis_balance_exchange_code(market_code)
    try:
        result = await _request_paged_json(
            "/uapi/overseas-stock/v1/trading/inquire-ccnl",
            "VTTS3035R" if settings.is_paper_trading else "TTTS3035R",
            params={
                "CANO": cano,
                "ACNT_PRDT_CD": acnt_prdt_cd,
                "OVRS_EXCG_CD": overseas_exchange,
                "PDNO": "",
                "ORD_STRT_DT": today,
                "ORD_END_DT": today,
                "SLL_BUY_DVSN": "00",
                "CCLD_NCCS_DVSN": "00",
                "SORT_SQN": "DS",
                "ORD_DT": "",
                "ORD_GNO_BRNO": "",
                "ODNO": "",
                "CTX_AREA_FK200": "",
                "CTX_AREA_NK200": "",
            },
            output_keys=("output", "output1", "output2"),
        )
        result["success"] = result.get("rt_cd") == "0"
        result["market"] = market_code
        result["currency"] = market_currency(market_code)
        return result
    except Exception as e:
        logger.error("해외 주문내역 조회 오류 ({}): {}", market_code, str(e))
        return {"success": False, "error": str(e), "output": []}


async def place_overseas_order(
    symbol: str,
    side: str,
    quantity: int,
    price: float | None = None,
    market: str = "NASDAQ",
) -> dict:
    """해외주식 주문 실행"""
    market_code = normalize_market(market)
    cano, acnt_prdt_cd = _get_account_parts()
    is_buy = str(side).upper() == "BUY"
    tr_id = (
        "VTTT1002U" if settings.is_paper_trading and is_buy
        else "VTTT1006U" if settings.is_paper_trading and not is_buy
        else "TTTT1002U" if is_buy
        else "TTTT1006U"
    )

    try:
        result = await _request_json(
            "/uapi/overseas-stock/v1/trading/order",
            tr_id,
            params={
                "CANO": cano,
                "ACNT_PRDT_CD": acnt_prdt_cd,
                "OVRS_EXCG_CD": kis_order_exchange_code(market_code),
                "PDNO": symbol,
                "ORD_QTY": str(quantity),
                "OVRS_ORD_UNPR": f"{price or 0}",
                "CTAC_TLNO": "",
                "MGCO_APTM_ODNO": "",
                "SLL_TYPE": "00",
                "ORD_SVR_DVSN_CD": "0",
                "ORD_DVSN": "00" if price else "31",
            },
            method="POST",
            use_trading_domain=True,
        )
        result["success"] = result.get("rt_cd") == "0"
        result["market"] = market_code
        result["currency"] = market_currency(market_code)
        return result
    except Exception as e:
        logger.error("해외 주문 오류 ({} {}): {}", market_code, symbol, str(e))
        return {"success": False, "error": str(e), "output": {}}


async def cancel_overseas_order(
    symbol: str,
    order_id: str,
    quantity: int,
    price: float | None = None,
    market: str = "NASDAQ",
) -> dict:
    """해외주식 정정/취소"""
    market_code = normalize_market(market)
    cano, acnt_prdt_cd = _get_account_parts()
    try:
        result = await _request_json(
            "/uapi/overseas-stock/v1/trading/order-rvsecncl",
            "VTTT1004U" if settings.is_paper_trading else "TTTT1004U",
            params={
                "CANO": cano,
                "ACNT_PRDT_CD": acnt_prdt_cd,
                "OVRS_EXCG_CD": kis_order_exchange_code(market_code),
                "PDNO": symbol,
                "ORGN_ODNO": order_id,
                "RVSE_CNCL_DVSN_CD": "02",
                "ORD_QTY": str(quantity),
                "OVRS_ORD_UNPR": f"{price or 0}",
                "CTAC_TLNO": "",
                "MGCO_APTM_ODNO": "",
                "ORD_SVR_DVSN_CD": "0",
            },
            method="POST",
            use_trading_domain=True,
        )
        result["success"] = result.get("rt_cd") == "0"
        result["market"] = market_code
        result["currency"] = market_currency(market_code)
        return result
    except Exception as e:
        logger.error("해외 주문취소 오류 ({} {}): {}", market_code, symbol, str(e))
        return {"success": False, "error": str(e), "output": {}}
