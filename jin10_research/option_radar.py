from __future__ import annotations

import hashlib
import json
import math
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from threading import RLock
from typing import Any, Dict, Iterable, List, Optional, Tuple


SINA_OPTION_PAGE = "https://stock.finance.sina.com.cn/futures/view/optionsDP.php"
SINA_OPTION_API = (
    "https://stock.finance.sina.com.cn/futures/api/openapi.php/"
    "OptionService.getOptionData"
)


@dataclass(frozen=True)
class Product:
    key: str
    name: str
    prefix: str
    product: str
    exchange: str
    exchange_id: str
    fallback_tick: float
    fallback_multiplier: float


PRODUCTS: Tuple[Product, ...] = (
    Product("oil", "原油", "sc", "sc_o", "ine", "INE", 0.05, 1000.0),
    Product("gold", "黄金", "au", "au_o", "shfe", "SHFE", 0.02, 1000.0),
    Product("silver", "白银", "ag", "ag_o", "shfe", "SHFE", 0.5, 15.0),
)
PRODUCT_BY_KEY = {product.key: product for product in PRODUCTS}


def _number(value: Any) -> Optional[float]:
    if value in (None, "", "-"):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _integer(value: Any) -> int:
    number = _number(value)
    return int(number) if number is not None else 0


def _round(value: Optional[float], digits: int = 4) -> Optional[float]:
    return round(value, digits) if value is not None and math.isfinite(value) else None


def _norm_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def _norm_pdf(value: float) -> float:
    return math.exp(-0.5 * value * value) / math.sqrt(2.0 * math.pi)


def black76_price(
    future: float,
    strike: float,
    years: float,
    volatility: float,
    rate: float,
    option_type: str,
) -> float:
    """European futures-option approximation used only for live screening."""
    if min(future, strike, years, volatility) <= 0:
        return 0.0
    sigma_root_t = volatility * math.sqrt(years)
    d1 = (math.log(future / strike) + 0.5 * volatility * volatility * years) / sigma_root_t
    d2 = d1 - sigma_root_t
    discount = math.exp(-rate * years)
    if option_type == "CALL":
        return discount * (future * _norm_cdf(d1) - strike * _norm_cdf(d2))
    return discount * (strike * _norm_cdf(-d2) - future * _norm_cdf(-d1))


def implied_volatility(
    price: float,
    future: float,
    strike: float,
    years: float,
    rate: float,
    option_type: str,
) -> Optional[float]:
    if min(price, future, strike, years) <= 0:
        return None
    low, high = 0.001, 5.0
    low_price = black76_price(future, strike, years, low, rate, option_type)
    high_price = black76_price(future, strike, years, high, rate, option_type)
    if price < low_price - 1e-8 or price > high_price + 1e-8:
        return None
    for _ in range(64):
        middle = (low + high) / 2.0
        estimate = black76_price(future, strike, years, middle, rate, option_type)
        if estimate < price:
            low = middle
        else:
            high = middle
    return (low + high) / 2.0


def black76_greeks(
    future: float,
    strike: float,
    years: float,
    volatility: float,
    rate: float,
    option_type: str,
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    if min(future, strike, years, volatility) <= 0:
        return None, None, None
    root_t = math.sqrt(years)
    d1 = (math.log(future / strike) + 0.5 * volatility * volatility * years) / (
        volatility * root_t
    )
    discount = math.exp(-rate * years)
    delta = discount * _norm_cdf(d1)
    if option_type == "PUT":
        delta -= discount
    gamma = discount * _norm_pdf(d1) / (future * volatility * root_t)
    vega_per_point = discount * future * _norm_pdf(d1) * root_t / 100.0
    return delta, gamma, vega_per_point


def black76_theta_per_day(
    future: float,
    strike: float,
    years: float,
    volatility: float,
    rate: float,
    option_type: str,
) -> Optional[float]:
    """One calendar day's model price change with futures and IV held constant."""
    if min(future, strike, years, volatility) <= 0:
        return None
    current = black76_price(future, strike, years, volatility, rate, option_type)
    remaining = max(years - 1.0 / 365.0, 1.0 / (365.0 * 24.0))
    next_day = black76_price(future, strike, remaining, volatility, rate, option_type)
    return next_day - current


def _expiry_years(expiry: Optional[date], now: datetime) -> Optional[float]:
    if expiry is None:
        return None
    expiry_at = datetime.combine(expiry, time(15, 0), tzinfo=now.tzinfo)
    seconds = (expiry_at - now).total_seconds()
    return max(seconds, 0.0) / (365.0 * 24.0 * 3600.0)


def _expiry_fit(days: Optional[int]) -> float:
    if days is None:
        return 0.6
    if days < 1:
        return 0.0
    if days <= 2:
        return 0.08
    if days <= 4:
        return 0.35
    if days <= 7:
        return 0.7
    if days <= 45:
        return 1.0
    if days <= 90:
        return 0.85
    if days <= 180:
        return 0.6
    return 0.35


def _gamma_expiry_fit(days: Optional[int]) -> float:
    if days is None:
        return 0.5
    if days < 2:
        return 0.05
    if days <= 5:
        return 0.65
    if days <= 21:
        return 1.0
    if days <= 45:
        return 0.7
    return 0.3


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _median(values: Iterable[Optional[float]]) -> Optional[float]:
    clean = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    return statistics.median(clean) if clean else None


def _surface_context(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Optional[float]]]:
    """Build expiry-level ATM IV, 25-delta risk reversal, and relative term value."""
    by_underlying: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        by_underlying.setdefault(str(row.get("underlying") or ""), []).append(row)

    context: Dict[str, Dict[str, Optional[float]]] = {}
    for underlying, group in by_underlying.items():
        valid = [
            row for row in group
            if row.get("iv_pct") is not None
            and row.get("delta") is not None
            and row.get("valid_quote")
            and float(row.get("spread_fraction") or 1.0) <= 0.12
            and abs(float(row.get("moneyness_pct") or 100.0)) <= 20.0
        ]
        near_atm = sorted(valid, key=lambda row: abs(float(row.get("moneyness_pct") or 100.0)))[:6]
        calls = [row for row in valid if row.get("option_type") == "CALL"]
        puts = [row for row in valid if row.get("option_type") == "PUT"]
        call25 = min(calls, key=lambda row: abs(abs(float(row["delta"])) - 0.25), default=None)
        put25 = min(puts, key=lambda row: abs(abs(float(row["delta"])) - 0.25), default=None)
        rr25 = None
        if call25 and put25:
            rr25 = float(call25["iv_pct"]) - float(put25["iv_pct"])
        context[underlying] = {
            "atm_iv_pct": _median(row.get("iv_pct") for row in near_atm),
            "rr25_pct": rr25,
            "days_to_expiry": _median(row.get("days_to_expiry") for row in group),
            "term_premium_pct": None,
            "score_term": 60.0,
        }

    by_asset: Dict[str, List[Tuple[str, Dict[str, Optional[float]]]]] = {}
    for underlying, item in context.items():
        asset = next(
            (str(row.get("asset")) for row in rows if row.get("underlying") == underlying),
            "",
        )
        if item.get("atm_iv_pct") is not None and item.get("days_to_expiry") is not None:
            by_asset.setdefault(asset, []).append((underlying, item))
    for items in by_asset.values():
        items.sort(key=lambda pair: float(pair[1]["days_to_expiry"] or 0.0))
        for index, (_, item) in enumerate(items):
            peers = []
            if index > 0:
                peers.append(float(items[index - 1][1]["atm_iv_pct"] or 0.0))
            if index + 1 < len(items):
                peers.append(float(items[index + 1][1]["atm_iv_pct"] or 0.0))
            if not peers:
                continue
            benchmark = statistics.mean(peers)
            premium = float(item["atm_iv_pct"] or 0.0) - benchmark
            item["term_premium_pct"] = premium
            item["score_term"] = _clamp(65.0 - 5.0 * premium, 20.0, 95.0)
    return context


def _realized_volatility(closes: List[float], window: int) -> Optional[float]:
    sample = closes[-(window + 1):]
    if len(sample) < min(window + 1, 6):
        return None
    returns = [math.log(current / previous) for previous, current in zip(sample, sample[1:]) if previous > 0]
    if len(returns) < 5:
        return None
    return statistics.stdev(returns) * math.sqrt(252.0) * 100.0


def _history_context(
    records: List[Dict[str, Any]],
    live_price: Optional[float],
    previous_settle: Optional[float],
) -> Dict[str, Optional[float]]:
    clean = []
    for record in records:
        close = _number(record.get("close"))
        high = _number(record.get("high"))
        low = _number(record.get("low"))
        if close is not None and high is not None and low is not None and close > 0:
            clean.append({"close": close, "high": high, "low": low})
    closes = [item["close"] for item in clean]
    rv5 = _realized_volatility(closes, 5)
    rv20 = _realized_volatility(closes, 20)
    rv60 = _realized_volatility(closes, 60)
    forecast_rv = None
    if rv5 is not None and rv20 is not None:
        forecast_rv = 0.65 * rv5 + 0.35 * rv20
    else:
        forecast_rv = rv20 or rv5 or rv60

    recent = clean[-20:]
    price = live_price or (closes[-1] if closes else None)
    atr_abs = None
    if recent:
        true_ranges = []
        previous = closes[-len(recent) - 1] if len(closes) > len(recent) else recent[0]["close"]
        for item in recent:
            true_ranges.append(
                max(
                    item["high"] - item["low"],
                    abs(item["high"] - previous),
                    abs(item["low"] - previous),
                )
            )
            previous = item["close"]
        atr_abs = statistics.mean(true_ranges)
    atr_pct = atr_abs / price * 100.0 if atr_abs and price else None
    sma20 = statistics.mean(item["close"] for item in recent) if recent else None
    trend_z = (price - sma20) / atr_abs if price and sma20 and atr_abs else None
    high20 = max((item["high"] for item in recent), default=None)
    low20 = min((item["low"] for item in recent), default=None)
    breakout_direction = None
    if price is not None and high20 is not None and low20 is not None and high20 > low20:
        if price > high20:
            breakout_direction = 1.0
        elif price < low20:
            breakout_direction = -1.0
        else:
            breakout_direction = _clamp(2.0 * (price - low20) / (high20 - low20) - 1.0, -1.0, 1.0)
    change_pct = (
        (price / previous_settle - 1.0) * 100.0
        if price is not None and previous_settle
        else None
    )
    day_move_z = change_pct / atr_pct if change_pct is not None and atr_pct else None
    return {
        "rv5_pct": _round(rv5, 2),
        "rv20_pct": _round(rv20, 2),
        "rv60_pct": _round(rv60, 2),
        "forecast_rv_pct": _round(forecast_rv, 2),
        "rv_ratio": _round(rv5 / rv20, 4) if rv5 is not None and rv20 else None,
        "atr20_pct": _round(atr_pct, 3),
        "trend_z": _round(trend_z, 4),
        "day_move_z": _round(day_move_z, 4),
        "breakout_direction": _round(breakout_direction, 4),
        "sma20": _round(sma20, 4),
        "high20": _round(high20, 4),
        "low20": _round(low20, 4),
    }


def _attach_buyer_market_metrics(
    rows: List[Dict[str, Any]],
    contexts: Dict[str, Dict[str, Optional[float]]],
    rate: float,
) -> None:
    for row in rows:
        context = contexts.get(str(row.get("underlying") or ""), {})
        row.update(context)
        forecast_rv = context.get("forecast_rv_pct")
        days = row.get("days_to_expiry")
        iv_pct = row.get("iv_pct")
        future = row.get("underlying_price")
        strike = row.get("strike")
        ask = row.get("ask")
        if (
            forecast_rv is None
            or days is None
            or iv_pct is None
            or future is None
            or strike is None
            or ask is None
            or float(ask) <= 0
        ):
            row["scenario_days"] = None
            row["target_move_pct"] = None
            row["scenario_roi_pct"] = None
            row["breakeven_coverage"] = None
            continue
        horizon = min(5, max(1, int(days) - 1))
        target_move_pct = 0.75 * float(forecast_rv) * math.sqrt(horizon / 252.0)
        side_sign = 1.0 if row.get("option_type") == "CALL" else -1.0
        scenario_future = float(future) * (1.0 + side_sign * target_move_pct / 100.0)
        remaining_years = max((int(days) - horizon) / 365.0, 1.0 / 365.0)
        scenario_price = black76_price(
            scenario_future,
            float(strike),
            remaining_years,
            float(iv_pct) / 100.0,
            rate,
            str(row.get("option_type")),
        )
        spread = float(row.get("spread") or 0.0)
        conservative_exit = max(scenario_price - spread / 2.0, 0.0)
        scenario_roi = (conservative_exit / float(ask) - 1.0) * 100.0
        breakeven = (
            float(strike) + float(ask)
            if side_sign > 0
            else float(strike) - float(ask)
        )
        breakeven_distance = max(side_sign * (breakeven - float(future)) / float(future) * 100.0, 0.0)
        row["scenario_days"] = horizon
        row["target_move_pct"] = _round(target_move_pct, 2)
        row["scenario_roi_pct"] = _round(scenario_roi, 1)
        row["breakeven_distance_pct"] = _round(breakeven_distance, 2)
        row["breakeven_coverage"] = (
            _round(target_move_pct / breakeven_distance, 2)
            if breakeven_distance > 0
            else None
        )


def score_option_rows(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Attach execution and buyer-specific, explainable scores to option quotes."""
    scored = [dict(row) for row in rows]
    surface = _surface_context(scored)
    by_asset: Dict[str, List[Dict[str, Any]]] = {}
    for row in scored:
        by_asset.setdefault(str(row["asset"]), []).append(row)

    for asset_rows in by_asset.values():
        max_oi = max((int(row.get("open_interest") or 0) for row in asset_rows), default=1)
        max_depth = max((int(row.get("depth") or 0) for row in asset_rows), default=1)
        max_gamma = max((float(row.get("gamma") or 0.0) for row in asset_rows), default=0.0)
        oi_denominator = math.log1p(max(max_oi, 1))
        depth_denominator = math.log1p(max(max_depth, 1))

        for row in asset_rows:
            spread_fraction = float(row.get("spread_fraction") or 1.0)
            spread_quality = 1.0 / (1.0 + 14.0 * max(spread_fraction, 0.0))
            oi_quality = math.log1p(max(int(row.get("open_interest") or 0), 0)) / oi_denominator
            depth_quality = math.log1p(max(int(row.get("depth") or 0), 0)) / depth_denominator
            liquidity = 100.0 * (
                0.55 * spread_quality + 0.30 * oi_quality + 0.15 * depth_quality
            )

            delta = row.get("delta")
            abs_delta = abs(float(delta)) if delta is not None else None
            delta_fit = (
                max(0.0, 1.0 - abs(abs_delta - 0.50) / 0.45)
                if abs_delta is not None
                else 0.35
            )
            atm_distance = abs(float(row.get("moneyness_pct") or 100.0))
            atm_fit = math.exp(-atm_distance / 8.0)
            expiry_fit = _expiry_fit(row.get("days_to_expiry"))
            gamma = float(row.get("gamma") or 0.0)
            gamma_quality = gamma / max_gamma if max_gamma > 0 else 0.0

            balanced = (
                0.55 * liquidity
                + 20.0 * delta_fit
                + 15.0 * expiry_fit
                + 10.0 * atm_fit
            )
            gamma_score = 100.0 * (
                0.42 * (liquidity / 100.0)
                + 0.28 * gamma_quality
                + 0.20 * atm_fit
                + 0.10 * _gamma_expiry_fit(row.get("days_to_expiry"))
            )

            valid_quote = bool(row.get("valid_quote"))
            days = row.get("days_to_expiry")
            tradable = (
                valid_quote
                and int(row.get("open_interest") or 0) > 0
                and spread_fraction <= 0.50
                and (days is None or days >= 1)
            )
            row["tradable"] = tradable
            row["score_liquidity"] = round(liquidity, 1)
            row["score_balanced"] = round(balanced, 1)
            row["score_gamma"] = round(gamma_score, 1)

            surface_item = surface.get(str(row.get("underlying") or ""), {})
            atm_iv = surface_item.get("atm_iv_pct")
            rr25 = surface_item.get("rr25_pct")
            row["atm_iv_pct"] = _round(atm_iv, 2)
            row["rr25_pct"] = _round(rr25, 2)
            row["term_premium_pct"] = _round(surface_item.get("term_premium_pct"), 2)
            row["score_term"] = round(float(surface_item.get("score_term") or 60.0), 1)

            side_sign = 1.0 if row.get("option_type") == "CALL" else -1.0
            trend_z = float(row.get("trend_z") or 0.0)
            day_move_z = float(row.get("day_move_z") or 0.0)
            trend_component = _clamp(50.0 + 18.0 * side_sign * trend_z)
            day_component = _clamp(50.0 + 22.0 * side_sign * day_move_z)
            skew_component = (
                _clamp(50.0 + 5.0 * side_sign * float(rr25))
                if rr25 is not None
                else 50.0
            )
            direction_score = 0.50 * trend_component + 0.25 * day_component + 0.25 * skew_component

            iv_pct = row.get("iv_pct")
            forecast_rv = row.get("forecast_rv_pct")
            vol_edge = (
                float(forecast_rv) - float(iv_pct)
                if forecast_rv is not None and iv_pct is not None
                else None
            )
            vrp_score = _clamp(60.0 + 3.0 * float(vol_edge)) if vol_edge is not None else 50.0
            surface_premium = (
                float(iv_pct) - float(atm_iv)
                if iv_pct is not None and atm_iv is not None
                else None
            )
            surface_value_score = (
                _clamp(70.0 - 5.0 * max(surface_premium, 0.0) + 2.0 * max(-surface_premium, 0.0))
                if surface_premium is not None
                else 50.0
            )
            vol_value_score = 0.75 * vrp_score + 0.25 * surface_value_score

            rv_ratio = row.get("rv_ratio")
            rv_activation = (
                _clamp(50.0 + 100.0 * (float(rv_ratio) - 1.0))
                if rv_ratio is not None
                else 50.0
            )
            breakout_direction = float(row.get("breakout_direction") or 0.0)
            breakout_component = _clamp(50.0 + 45.0 * side_sign * breakout_direction)
            activation_score = 0.60 * rv_activation + 0.40 * breakout_component

            theta_burden = row.get("theta_burden_pct")
            theta_score = (
                _clamp(100.0 - 30.0 * float(theta_burden))
                if theta_burden is not None
                else 35.0
            )
            scenario_roi = row.get("scenario_roi_pct")
            scenario_score = (
                _clamp(45.0 + 1.4 * float(scenario_roi))
                if scenario_roi is not None
                else 35.0
            )
            efficiency_score = 100.0 * 0.35 * delta_fit + 0.25 * theta_score + 0.40 * scenario_score

            buyer_score = (
                0.25 * direction_score
                + 0.25 * vol_value_score
                + 0.20 * efficiency_score
                + 0.15 * activation_score
                + 0.10 * float(row["score_term"])
                + 0.05 * liquidity
            )

            spread_limit = 0.10 if row.get("asset") == "silver" else 0.08
            hard_blockers = []
            if not valid_quote:
                hard_blockers.append("缺少有效双边报价")
            if int(row.get("open_interest") or 0) <= 0:
                hard_blockers.append("无持仓量")
            if spread_fraction > spread_limit:
                hard_blockers.append("价差超过买方上限")
            if days is None:
                hard_blockers.append("到期日未知")
            elif not 14 <= int(days) <= 60:
                hard_blockers.append("不在14–60天买方窗口")
            if abs_delta is None or not 0.35 <= abs_delta <= 0.60:
                hard_blockers.append("Delta不在0.35–0.60")
            if atm_distance > 12.0:
                hard_blockers.append("行权价偏离标的超过12%")
            if iv_pct is None:
                hard_blockers.append("IV无法估算")
            if theta_burden is None or float(theta_burden) > 2.0:
                hard_blockers.append("每日Theta负担超过2%")

            signal_flags = {
                "direction": direction_score >= 60.0,
                "vol_value": vol_value_score >= 55.0,
                "activation": activation_score >= 55.0,
                "term": float(row["score_term"]) >= 55.0,
                "efficiency": efficiency_score >= 55.0,
                "liquidity": liquidity >= 60.0,
            }
            signal_count = sum(bool(value) for value in signal_flags.values())
            buyer_eligible = not hard_blockers
            core_pass = (
                direction_score >= 60.0
                and vol_value_score >= 55.0
                and efficiency_score >= 55.0
            )
            if buyer_eligible and core_pass and signal_count >= 4 and buyer_score >= 72.0:
                recommendation = "RECOMMENDED"
            elif buyer_eligible and direction_score >= 52.0 and buyer_score >= 60.0:
                recommendation = "WATCH"
            else:
                recommendation = "REJECT"

            row["buyer_eligible"] = buyer_eligible
            row["recommendation"] = recommendation
            row["signal_flags"] = signal_flags
            row["signal_count"] = signal_count
            row["hard_blockers"] = hard_blockers
            row["score_direction"] = round(direction_score, 1)
            row["score_vol_value"] = round(vol_value_score, 1)
            row["score_activation"] = round(activation_score, 1)
            row["score_efficiency"] = round(efficiency_score, 1)
            row["score_buyer"] = round(buyer_score, 1)
            row["vol_edge_pct"] = _round(vol_edge, 2)
            row["surface_premium_pct"] = _round(surface_premium, 2)

            reasons = []
            warnings = []
            if valid_quote:
                reasons.append("双边报价")
            reasons.append("价差{:.1f}%".format(spread_fraction * 100.0))
            reasons.append("持仓{}手".format(int(row.get("open_interest") or 0)))
            if abs_delta is not None:
                reasons.append("|Delta| {:.2f}".format(abs_delta))
            if days is not None:
                reasons.append("剩余{}天".format(days))
                if days <= 2:
                    warnings.append("临近到期")
            if spread_fraction > 0.12:
                warnings.append("价差偏宽")
            if int(row.get("open_interest") or 0) < 20:
                warnings.append("持仓较少")
            if not valid_quote:
                warnings.append("缺少有效双边报价")
            if row.get("iv_pct") is None:
                warnings.append("IV无法估算")
            if vol_edge is not None:
                reasons.append("IV-RV {:+.1f}点".format(-float(vol_edge)))
            if theta_burden is not None:
                reasons.append("Theta {:.2f}%/日".format(float(theta_burden)))
            if scenario_roi is not None:
                reasons.append("情景回报 {:+.0f}%".format(float(scenario_roi)))
            if hard_blockers:
                warnings.extend(item for item in hard_blockers if item not in warnings)
            row["reasons"] = reasons
            row["warnings"] = warnings

    recommendation_priority = {"RECOMMENDED": 3, "WATCH": 2, "REJECT": 0}
    scored.sort(
        key=lambda row: (
            recommendation_priority.get(str(row.get("recommendation")), 0),
            float(row.get("score_buyer") or 0),
        ),
        reverse=True,
    )
    return scored


class AKShareOptionRadar:
    """Fetch and score all current oil, gold, and silver option chains."""

    def __init__(self, risk_free_rate: float = 0.015, timeout: float = 12.0):
        self.risk_free_rate = risk_free_rate
        self.timeout = timeout
        self._lock = RLock()
        self._universe: Dict[str, List[str]] = {}
        self._universe_loaded_at: Optional[datetime] = None
        self._metadata: Dict[str, Dict[str, Any]] = {}
        self._metadata_loaded_at: Optional[datetime] = None
        self._history: Dict[str, List[Dict[str, Any]]] = {}
        self._history_loaded_at: Dict[str, datetime] = {}
        self._chain_hashes: Dict[str, str] = {}
        self._chain_changed_at: Dict[str, str] = {}

    @staticmethod
    def _imports():
        try:
            import akshare as ak
            import requests
            from bs4 import BeautifulSoup
        except ImportError as exc:
            raise RuntimeError(
                "Option radar requires akshare, requests, and beautifulsoup4."
            ) from exc
        return ak, requests, BeautifulSoup

    def _load_universe(self, now: datetime) -> Dict[str, List[str]]:
        with self._lock:
            if (
                self._universe
                and self._universe_loaded_at
                and now - self._universe_loaded_at < timedelta(minutes=10)
            ):
                return dict(self._universe)

        _, requests, BeautifulSoup = self._imports()
        universe: Dict[str, List[str]] = {}
        for product in PRODUCTS:
            url = "{}/{}/{}".format(SINA_OPTION_PAGE, product.product, product.exchange)
            response = requests.get(url, timeout=self.timeout)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "lxml")
            contracts = [
                item.get_text(strip=True)
                for item in soup.select("#option_suffix li")
                if item.get_text(strip=True).lower().startswith(product.prefix)
            ]
            if not contracts:
                raise RuntimeError("No current {} option contracts returned".format(product.name))
            universe[product.key] = list(dict.fromkeys(contracts))

        with self._lock:
            self._universe = universe
            self._universe_loaded_at = now
        return dict(universe)

    def _load_metadata(self, now: datetime) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            if (
                self._metadata
                and self._metadata_loaded_at
                and now - self._metadata_loaded_at < timedelta(hours=6)
            ):
                return dict(self._metadata)

        ak, _, _ = self._imports()
        metadata: Dict[str, Dict[str, Any]] = {}
        try:
            frame = ak.option_contract_info_ctp()
            wanted = {product.exchange_id for product in PRODUCTS}
            for item in frame.to_dict("records"):
                instrument = str(item.get("合约ID") or "")
                exchange_id = str(item.get("交易所ID") or "")
                if exchange_id not in wanted or not instrument:
                    continue
                expiry = None
                expiry_text = str(item.get("最后交易日") or "")
                try:
                    expiry = date.fromisoformat(expiry_text[:10])
                except ValueError:
                    pass
                metadata[instrument.lower()] = {
                    "expiry": expiry,
                    "tick": _number(item.get("最小变动价位")),
                    "multiplier": _number(item.get("合约乘数")),
                    "underlying": str(item.get("标的合约ID") or "").lower(),
                }
        except Exception:
            metadata = {}

        with self._lock:
            self._metadata = metadata
            self._metadata_loaded_at = now
        return dict(metadata)

    def _fetch_underlyings(self, contracts: List[str]) -> Dict[str, Dict[str, Any]]:
        ak, _, _ = self._imports()
        if not contracts:
            return {}
        codes = [contract.upper() for contract in contracts]
        frame = ak.futures_zh_spot(symbol=",".join(codes), market="CF", adjust="0")
        records = frame.to_dict("records")
        if len(records) != len(codes):
            result: Dict[str, Dict[str, Any]] = {}
            for contract, code in zip(contracts, codes):
                single = ak.futures_zh_spot(symbol=code, market="CF", adjust="0")
                if not single.empty:
                    result[contract] = single.iloc[0].to_dict()
            return result
        return {contract: record for contract, record in zip(contracts, records)}

    def _load_history(self, contract: str, now: datetime) -> List[Dict[str, Any]]:
        with self._lock:
            loaded_at = self._history_loaded_at.get(contract)
            if (
                contract in self._history
                and loaded_at is not None
                and now - loaded_at < timedelta(minutes=30)
            ):
                return list(self._history[contract])
        ak, _, _ = self._imports()
        frame = ak.futures_zh_daily_sina(symbol=contract.upper())
        records = frame.tail(90).to_dict("records") if not frame.empty else []
        with self._lock:
            self._history[contract] = records
            self._history_loaded_at[contract] = now
        return list(records)

    def _fetch_chain(self, product: Product, contract: str) -> Tuple[str, Dict[str, Any]]:
        _, requests, _ = self._imports()
        params = {
            "type": "futures",
            "product": product.product,
            "exchange": product.exchange,
            "pinzhong": contract,
        }
        response = requests.get(SINA_OPTION_API, params=params, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        result = payload.get("result") or {}
        status = result.get("status") or {}
        data = result.get("data") or {}
        if status.get("code") != 0 or not data.get("up") or not data.get("down"):
            raise RuntimeError("Invalid option chain response for {}".format(contract))
        digest = hashlib.sha256(
            json.dumps([data["up"], data["down"]], ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        return digest, data

    def _normalize_chain(
        self,
        product: Product,
        contract: str,
        chain: Dict[str, Any],
        underlying: Dict[str, Any],
        metadata: Dict[str, Dict[str, Any]],
        now: datetime,
    ) -> List[Dict[str, Any]]:
        future = _number(underlying.get("current_price"))
        if future is None or future <= 0:
            return []
        rows: List[Dict[str, Any]] = []
        for call, put in zip(chain.get("up") or [], chain.get("down") or []):
            if len(call) < 9 or len(put) < 8:
                continue
            strike = _number(call[7])
            if strike is None or strike <= 0:
                continue
            legs = (
                ("CALL", call[8], call[0], call[1], call[2], call[3], call[4], call[5]),
                ("PUT", put[7], put[0], put[1], put[2], put[3], put[4], put[5]),
            )
            for option_type, instrument, bid_volume, bid, last, ask, ask_volume, oi in legs:
                instrument = str(instrument).lower()
                bid_price = _number(bid)
                ask_price = _number(ask)
                last_price = _number(last)
                valid_quote = bool(
                    bid_price is not None
                    and ask_price is not None
                    and bid_price > 0
                    and ask_price >= bid_price
                )
                mid = (bid_price + ask_price) / 2.0 if valid_quote else last_price
                spread = ask_price - bid_price if valid_quote else None
                spread_fraction = spread / mid if spread is not None and mid and mid > 0 else None

                meta = metadata.get(instrument) or {}
                expiry = meta.get("expiry")
                days_to_expiry = (expiry - now.date()).days if expiry else None
                years = _expiry_years(expiry, now)
                iv = (
                    implied_volatility(
                        mid,
                        future,
                        strike,
                        years,
                        self.risk_free_rate,
                        option_type,
                    )
                    if mid is not None and years is not None and years > 0
                    else None
                )
                delta = gamma = vega = None
                theta = None
                if iv is not None and years is not None:
                    delta, gamma, vega = black76_greeks(
                        future,
                        strike,
                        years,
                        iv,
                        self.risk_free_rate,
                        option_type,
                    )
                    theta = black76_theta_per_day(
                        future,
                        strike,
                        years,
                        iv,
                        self.risk_free_rate,
                        option_type,
                    )
                tick = _number(meta.get("tick")) or product.fallback_tick
                intrinsic = max(future - strike, 0.0) if option_type == "CALL" else max(strike - future, 0.0)
                rows.append(
                    {
                        "asset": product.key,
                        "asset_name": product.name,
                        "underlying": contract,
                        "underlying_price": _round(future, 4),
                        "instrument": instrument,
                        "option_type": option_type,
                        "strike": _round(strike, 4),
                        "expiry": expiry.isoformat() if expiry else None,
                        "days_to_expiry": days_to_expiry,
                        "bid": _round(bid_price, 4),
                        "ask": _round(ask_price, 4),
                        "last": _round(last_price, 4),
                        "mid": _round(mid, 4),
                        "bid_volume": _integer(bid_volume),
                        "ask_volume": _integer(ask_volume),
                        "depth": _integer(bid_volume) + _integer(ask_volume),
                        "open_interest": _integer(oi),
                        "valid_quote": valid_quote,
                        "spread": _round(spread, 4),
                        "spread_fraction": _round(spread_fraction, 8),
                        "spread_pct": _round(spread_fraction * 100.0, 3) if spread_fraction is not None else None,
                        "spread_ticks": _round(spread / tick, 1) if spread is not None and tick else None,
                        "moneyness_pct": _round((strike / future - 1.0) * 100.0, 3),
                        "intrinsic": _round(intrinsic, 4),
                        "time_value": _round(max((mid or 0.0) - intrinsic, 0.0), 4),
                        "iv_pct": _round(iv * 100.0, 2) if iv is not None else None,
                        "delta": _round(delta, 4),
                        "gamma": _round(gamma, 8),
                        "vega": _round(vega, 4),
                        "theta": _round(theta, 4),
                        "theta_burden_pct": (
                            _round(abs(theta) / mid * 100.0, 3)
                            if theta is not None and mid
                            else None
                        ),
                        "tick": tick,
                        "multiplier": _number(meta.get("multiplier")) or product.fallback_multiplier,
                    }
                )
        return rows

    def snapshot(self) -> Dict[str, Any]:
        now = datetime.now().astimezone()
        universe = self._load_universe(now)
        metadata = self._load_metadata(now)
        all_contracts = [contract for contracts in universe.values() for contract in contracts]
        underlyings = self._fetch_underlyings(all_contracts)

        histories: Dict[str, List[Dict[str, Any]]] = {}
        history_errors = []
        with ThreadPoolExecutor(max_workers=4) as executor:
            history_jobs = {
                executor.submit(self._load_history, contract, now): contract
                for contract in all_contracts
            }
            for future_job in as_completed(history_jobs):
                contract = history_jobs[future_job]
                try:
                    histories[contract] = future_job.result()
                except Exception as exc:
                    history_errors.append({"contract": contract, "error": str(exc)})

        market_contexts: Dict[str, Dict[str, Optional[float]]] = {}
        for contract in all_contracts:
            quote = underlyings.get(contract) or {}
            market_contexts[contract] = _history_context(
                histories.get(contract, []),
                _number(quote.get("current_price")),
                _number(quote.get("last_settle_price")),
            )

        chain_results: Dict[Tuple[str, str], Tuple[str, Dict[str, Any]]] = {}
        errors = []
        jobs = []
        with ThreadPoolExecutor(max_workers=4) as executor:
            for asset, contracts in universe.items():
                product = PRODUCT_BY_KEY[asset]
                for contract in contracts:
                    jobs.append((asset, contract, executor.submit(self._fetch_chain, product, contract)))
            for asset, contract, future_job in jobs:
                try:
                    chain_results[(asset, contract)] = future_job.result()
                except Exception as exc:
                    errors.append({"contract": contract, "error": str(exc)})

        rows: List[Dict[str, Any]] = []
        chain_status = []
        for asset, contracts in universe.items():
            product = PRODUCT_BY_KEY[asset]
            for contract in contracts:
                result = chain_results.get((asset, contract))
                if not result:
                    continue
                digest, chain = result
                with self._lock:
                    if self._chain_hashes.get(contract) != digest:
                        self._chain_hashes[contract] = digest
                        self._chain_changed_at[contract] = now.isoformat()
                    changed_at = self._chain_changed_at.get(contract)
                normalized = self._normalize_chain(
                    product,
                    contract,
                    chain,
                    underlyings.get(contract) or {},
                    metadata,
                    now,
                )
                rows.extend(normalized)
                chain_status.append(
                    {
                        "asset": asset,
                        "contract": contract,
                        "option_count": len(normalized),
                        "last_observed_change": changed_at,
                    }
                )

        _attach_buyer_market_metrics(rows, market_contexts, self.risk_free_rate)
        scored = score_option_rows(rows)
        eligible = [row for row in scored if row.get("tradable")]
        buyer_eligible = [row for row in scored if row.get("buyer_eligible")]
        recommended = [row for row in scored if row.get("recommendation") == "RECOMMENDED"]
        watch = [row for row in scored if row.get("recommendation") == "WATCH"]
        summaries = []
        for product in PRODUCTS:
            futures = []
            for contract in universe.get(product.key, []):
                quote = underlyings.get(contract) or {}
                price = _number(quote.get("current_price"))
                if price is None:
                    continue
                previous = _number(quote.get("last_settle_price"))
                futures.append(
                    {
                        "contract": contract,
                        "price": _round(price, 4),
                        "bid": _round(_number(quote.get("bid_price")), 4),
                        "ask": _round(_number(quote.get("ask_price")), 4),
                        "open_interest": _integer(quote.get("hold")),
                        "volume": _integer(quote.get("volume")),
                        "quote_time": str(quote.get("time") or ""),
                        "change_pct": _round((price / previous - 1.0) * 100.0, 2)
                        if previous
                        else None,
                    }
                )
            active = max(futures, key=lambda item: item["open_interest"], default=None)
            active_contract = str((active or {}).get("contract") or "")
            active_rows = [row for row in scored if row.get("underlying") == active_contract]
            active_context = market_contexts.get(active_contract, {})
            atm_iv = _median(row.get("atm_iv_pct") for row in active_rows)
            rr25 = _median(row.get("rr25_pct") for row in active_rows)
            asset_recommended = [
                row for row in recommended if row.get("asset") == product.key
            ]
            best = max(
                asset_recommended,
                key=lambda row: float(row.get("score_buyer") or 0.0),
                default=None,
            )
            trend_z = float(active_context.get("trend_z") or 0.0)
            rr_bias = float(rr25 or 0.0)
            bias_value = 0.75 * trend_z + 0.05 * rr_bias
            market_bias = "bullish" if bias_value > 0.25 else "bearish" if bias_value < -0.25 else "neutral"
            summaries.append(
                {
                    "asset": product.key,
                    "asset_name": product.name,
                    "active": active,
                    "contracts": futures,
                    "fetched_options": sum(1 for row in rows if row["asset"] == product.key),
                    "eligible_options": sum(1 for row in eligible if row["asset"] == product.key),
                    "buyer_eligible_options": sum(1 for row in buyer_eligible if row["asset"] == product.key),
                    "recommended_options": len(asset_recommended),
                    "market_bias": market_bias,
                    "atm_iv_pct": _round(atm_iv, 2),
                    "rr25_pct": _round(rr25, 2),
                    "rv5_pct": active_context.get("rv5_pct"),
                    "rv20_pct": active_context.get("rv20_pct"),
                    "forecast_rv_pct": active_context.get("forecast_rv_pct"),
                    "iv_rv_spread_pct": (
                        _round(float(atm_iv) - float(active_context["forecast_rv_pct"]), 2)
                        if atm_iv is not None and active_context.get("forecast_rv_pct") is not None
                        else None
                    ),
                    "best_candidate": (
                        {
                            "instrument": best.get("instrument"),
                            "option_type": best.get("option_type"),
                            "score_buyer": best.get("score_buyer"),
                        }
                        if best
                        else None
                    ),
                }
            )

        if not rows:
            raise RuntimeError("No option quotes were normalized from the current chains")
        return {
            "status": "ok" if not errors and not history_errors else "partial",
            "generated_at": now.isoformat(),
            "source": "AKShare + Sina public quotes + OpenCTP metadata",
            "risk_free_rate": self.risk_free_rate,
            "model_note": "IV/Greeks use Black-76 as a screening approximation; listed options are American-style.",
            "score_note": "Buyer scores combine direction, volatility value, activation, term structure, contract efficiency, and liquidity; they are research rankings rather than guaranteed returns.",
            "universe": universe,
            "summaries": summaries,
            "chains": chain_status,
            "counts": {
                "fetched": len(rows),
                "eligible": len(eligible),
                "buyer_eligible": len(buyer_eligible),
                "recommended": len(recommended),
                "watch": len(watch),
                "contracts": len(chain_status),
                "errors": len(errors) + len(history_errors),
            },
            "errors": errors,
            "context_errors": history_errors,
            "options": eligible,
        }
