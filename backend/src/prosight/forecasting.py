"""Weekly demand forecast per SKU with LightGBM.

Direct multi-horizon model: one row per (SKU, forecast origin week, horizon h = 1..HORIZON),
features known at the origin, target = units sold h weeks later. A Poisson objective suits
unit counts.

Features: horizon; recent lags (1, 2, 4 weeks); rolling means (4, 8, 12) and volatility; short-
vs long-run trend; seasonality of the *target* week (week-of-year sin/cos and the same week last
year); unit price; category (categorical).

Before the model is used it is backtested: trained only on data up to 12 weeks before the end,
then scored on those last 12 weeks against a naive "average of the last 8 weeks" forecast.
Per-SKU backtest errors give the forecast uncertainty used for safety stock.
"""

import math
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

import lightgbm as lgb
import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .models import SalesWeek, Sku

HORIZON = 16  # weeks forecast ahead (covers the longest lead time + review + order cycle)
BACKTEST_WEEKS = 12
MIN_HISTORY = 12  # weeks of history needed before a week can be a forecast origin

FEATURES = [
    "horizon", "lag_1", "lag_2", "lag_4", "mean_4", "mean_8", "mean_12", "std_8", "trend",
    "target_woy_sin", "target_woy_cos", "same_week_last_year", "price", "category",
]
PARAMS = {
    "objective": "poisson",
    "learning_rate": 0.05,
    "num_leaves": 15,
    "min_data_in_leaf": 20,
    "feature_fraction": 0.9,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "seed": 490,
    "deterministic": True,
    "num_threads": 1,
    "verbose": -1,
}
ROUNDS = 400


@dataclass
class SkuForecast:
    sku_id: int
    weekly: list[float]  # forecast units for weeks 1..HORIZON after the last observed week
    sigma: float  # std-dev of weekly forecast error (backtest), floored
    backtest_wape: float | None
    history: list[int]  # last 26 weeks of actual units


@dataclass
class ForecastResult:
    trained_at: datetime
    last_week: date  # start of the last observed week
    forecast_weeks: list[date]
    backtest_wape: float
    naive_wape: float
    backtest_bias: float  # sum(forecast - actual) / sum(actual)
    train_rows: int
    feature_importance: dict[str, float]
    skus: dict[int, SkuForecast] = field(default_factory=dict)


def _panel(db: Session):
    skus = db.scalars(select(Sku).options(selectinload(Sku.category)).order_by(Sku.id)).all()
    weeks = sorted(db.scalars(select(SalesWeek.week_start).distinct()))
    week_index = {w: i for i, w in enumerate(weeks)}
    sku_index = {s.id: i for i, s in enumerate(skus)}
    units = np.zeros((len(skus), len(weeks)))
    for sku_id, week, u in db.execute(select(SalesWeek.sku_id, SalesWeek.week_start, SalesWeek.units)):
        units[sku_index[sku_id], week_index[week]] = u
    categories = sorted({s.category.name for s in skus})
    cat_code = np.array([categories.index(s.category.name) for s in skus], dtype=float)
    prices = np.array([s.unit_price for s in skus])
    return skus, weeks, units, prices, cat_code


def _rows(units, prices, cat_code, weeks, origins, horizons, with_target: bool):
    """Feature matrix for every SKU × origin × horizon (targets only where observed)."""
    X, y, keys = [], [], []
    n_weeks = units.shape[1]
    for i in range(units.shape[0]):
        series = units[i]
        for t in origins:
            past = series[: t + 1]
            m4, m8, m12 = past[-4:].mean(), past[-8:].mean(), past[-12:].mean()
            base = [past[-1], past[-2], past[-4], m4, m8, m12, past[-8:].std(), m4 / m12 if m12 else 1.0]
            for h in horizons:
                target_t = t + h
                if with_target and target_t >= n_weeks:
                    continue
                target_week = weeks[t] + timedelta(weeks=h)
                woy = target_week.isocalendar().week
                last_year = target_t - 52
                X.append([
                    h, *base,
                    math.sin(2 * math.pi * woy / 52), math.cos(2 * math.pi * woy / 52),
                    series[last_year] if 0 <= last_year <= t else np.nan,
                    prices[i], cat_code[i],
                ])
                y.append(series[target_t] if with_target else np.nan)
                keys.append((i, t, h))
    return np.array(X, dtype=float), np.array(y, dtype=float), keys


def _train(X, y) -> lgb.Booster:
    data = lgb.Dataset(X, y, feature_name=FEATURES, categorical_feature=["category"], free_raw_data=False)
    return lgb.train(PARAMS, data, num_boost_round=ROUNDS)


def train_and_forecast(db: Session) -> ForecastResult:
    skus, weeks, units, prices, cat_code = _panel(db)
    n_weeks = units.shape[1]
    horizons = range(1, HORIZON + 1)
    if n_weeks < MIN_HISTORY + BACKTEST_WEEKS + 1:
        raise ValueError("Not enough sales history to train the forecast model.")

    # --- Backtest: train on data up to the cutoff, score the following BACKTEST_WEEKS weeks.
    cutoff = n_weeks - 1 - BACKTEST_WEEKS
    train_origins = range(MIN_HISTORY - 1, cutoff)
    X, y, keys = _rows(units, prices, cat_code, weeks, train_origins, horizons, with_target=True)
    keep = [k for k, (i, t, h) in enumerate(keys) if t + h <= cutoff]
    model = _train(X[keep], y[keep])
    Xb, yb, kb = _rows(units, prices, cat_code, weeks, [cutoff], range(1, BACKTEST_WEEKS + 1), with_target=True)
    pred_b = model.predict(Xb)
    naive_b = np.array([units[i, cutoff - 7: cutoff + 1].mean() for i, _, _ in kb])
    actual_total = yb.sum()
    errors_by_sku: dict[int, list[float]] = {}
    actual_by_sku: dict[int, float] = {}
    for (i, _, _), p, a in zip(kb, pred_b, yb):
        errors_by_sku.setdefault(i, []).append(p - a)
        actual_by_sku[i] = actual_by_sku.get(i, 0.0) + a

    # --- Production model: all history, forecast from the last observed week.
    X_all, y_all, _ = _rows(units, prices, cat_code, weeks, range(MIN_HISTORY - 1, n_weeks - 1), horizons, with_target=True)
    final = _train(X_all, y_all)
    Xf, _, kf = _rows(units, prices, cat_code, weeks, [n_weeks - 1], horizons, with_target=False)
    pred_f = final.predict(Xf)

    result = ForecastResult(
        trained_at=datetime.now(UTC),
        last_week=weeks[-1],
        forecast_weeks=[weeks[-1] + timedelta(weeks=h) for h in horizons],
        backtest_wape=float(np.abs(pred_b - yb).sum() / actual_total),
        naive_wape=float(np.abs(naive_b - yb).sum() / actual_total),
        backtest_bias=float((pred_b - yb).sum() / actual_total),
        train_rows=len(y_all),
        feature_importance={
            name: round(float(v), 1)
            for name, v in sorted(zip(FEATURES, final.feature_importance("gain")), key=lambda kv: -kv[1])
        },
    )
    forecasts: dict[int, list[float]] = {}
    for (i, _, h), p in zip(kf, pred_f):
        forecasts.setdefault(i, [0.0] * HORIZON)[h - 1] = max(0.0, float(p))
    for i, sku in enumerate(skus):
        errs = np.array(errors_by_sku[i])
        level = float(units[i, -8:].mean())
        result.skus[sku.id] = SkuForecast(
            sku_id=sku.id,
            weekly=[round(v, 2) for v in forecasts[i]],
            sigma=max(float(np.sqrt((errs**2).mean())), 0.1 * level, 0.5),
            backtest_wape=float(np.abs(errs).sum() / actual_by_sku[i]) if actual_by_sku[i] else None,
            history=[int(u) for u in units[i, -26:]],
        )
    return result


_cache: dict[tuple, ForecastResult] = {}


def get_forecast(db: Session) -> ForecastResult:
    """Forecast for the current sales data; retrains only when the sales history changes."""
    key = tuple(db.execute(
        select(func.count(SalesWeek.id), func.max(SalesWeek.week_start), func.sum(SalesWeek.units))
    ).one())
    if key not in _cache:
        _cache.clear()
        _cache[key] = train_and_forecast(db)
    return _cache[key]
