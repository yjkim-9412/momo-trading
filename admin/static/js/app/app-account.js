// ── app-account.js — Account display for KRX/US markets (ES module) ──

import { state, API, formatAmount, formatSignedAmount, convertKrwToUsd } from './app-state.js';
import { fetchJSON, formatKRW, formatKRWFull, refreshIcons } from '../shared/admin-core.js';

// ── Account Info ──
export async function loadMarketAccountInfo() {
  var market = state.currentMarket;
  var marketParam = market === 'KRX' ? '' : '?market=' + market;
  try {
    var json = await fetchJSON(API + '/account/overview' + marketParam);
    var overview = json.data || {};
    renderBalance(overview.balance, market);
    renderHoldings(overview.holdings || [], market);
    renderPendingOrders(overview.pending_orders || [], market);
  } catch (err) {
    console.error('Account info error:', err);
    var el = document.getElementById('account-info');
    if (el) el.textContent = '\uC870\uD68C \uC2E4\uD328';
  }
}

// ── Balance helpers ──
function renderBalanceFailure(el, badgeEl, message) {
  if (badgeEl) badgeEl.classList.add('hidden');
  var row = document.createElement('div');
  row.className = 'text-sm text-red-400';
  row.textContent = message || '\uACC4\uC88C \uC870\uD68C \uC2E4\uD328';
  el.replaceChildren(row);
}

function hasMeaningfulDiff(left, right, threshold) {
  return Math.abs(Number(left || 0) - Number(right || 0)) >= threshold;
}

function shouldShowRawPnl(data) {
  if (!data || data.pnl_source !== 'HOLDINGS_SUM') return false;
  return hasMeaningfulDiff(data.raw_total_pnl, data.total_pnl, 1)
    || hasMeaningfulDiff(data.raw_total_pnl_rate, data.total_pnl_rate, 0.01);
}

function getKrxCashLabels(data) {
  if (data && data.cash_source === 'BROKER_ORDERABLE') {
    return {
      primary: '\uAC00\uC6A9\uD604\uAE08',
      secondary: '\uC608\uC218\uAE08',
    };
  }
  return {
    primary: '\uC608\uC218\uAE08',
    secondary: '\uAC00\uC6A9\uD604\uAE08',
  };
}

// ── Main balance renderer ──
export function renderBalance(data, market) {
  var el = document.getElementById('account-info');
  var badgeEl = document.getElementById('cash-source-badge');
  if (!el || !data) {
    if (el) el.textContent = '\uACC4\uC88C \uBBF8\uC5F0\uACB0';
    if (badgeEl) badgeEl.classList.add('hidden');
    return;
  }
  if (data.is_valid === false) {
    renderBalanceFailure(el, badgeEl, data.status_message);
    return;
  }
  var isUS = market !== 'KRX';
  var exchangeRate = Number(data.exchange_rate_to_krw || 0);
  var effectiveCash = Number(data.effective_cash != null ? data.effective_cash : (data.cash != null ? data.cash : 0));
  var rawCash = Number(data.raw_cash != null ? data.raw_cash : (data.cash != null ? data.cash : 0));
  var operatingCash = Number(
    data.operating_cash != null
      ? data.operating_cash
      : Math.max(Number(data.total_asset || 0) - Number(data.stock_value || 0), 0)
  );
  var totalPnl = Number(data.total_pnl != null ? data.total_pnl : 0);
  var totalPnlRate = Number(data.total_pnl_rate != null ? data.total_pnl_rate : 0);
  var rawTotalPnl = Number(data.raw_total_pnl != null ? data.raw_total_pnl : totalPnl);
  var rawTotalPnlRate = Number(data.raw_total_pnl_rate != null ? data.raw_total_pnl_rate : totalPnlRate);
  var pnlColor = totalPnl >= 0 ? 'text-green-400' : 'text-red-400';
  // Cash source badge (US only)
  if (badgeEl) {
    if (isUS) {
      badgeEl.classList.remove('hidden');
      badgeEl.textContent = data.cash_source === 'TOTAL_ASSET_PROXY' ? '\uCD1D\uC790\uC0B0 \uD504\uB85D\uC2DC' : '\uBE0C\uB85C\uCEE4 \uD604\uAE08';
      badgeEl.className = data.cash_source === 'TOTAL_ASSET_PROXY'
        ? 'px-2 py-0.5 rounded-full text-[11px] bg-sky-500/15 text-sky-300'
        : 'px-2 py-0.5 rounded-full text-[11px] bg-slate-800 text-gray-300';
    } else {
      badgeEl.classList.add('hidden');
    }
  }
  el.replaceChildren();
  if (isUS) {
    _renderBalanceUS(el, data, effectiveCash, rawCash, operatingCash, totalPnl, totalPnlRate, rawTotalPnl, rawTotalPnlRate, pnlColor, exchangeRate);
  } else {
    _renderBalanceKRX(el, data, effectiveCash, rawCash, operatingCash, totalPnl, totalPnlRate, rawTotalPnl, rawTotalPnlRate, pnlColor);
  }
}

function _renderBalanceUS(el, data, effectiveCash, rawCash, operatingCash, totalPnl, totalPnlRate, rawTotalPnl, rawTotalPnlRate, pnlColor, exchangeRate) {
  var totalAssetUsd = convertKrwToUsd(data.total_asset, exchangeRate);
  var effectiveCashUsd = convertKrwToUsd(effectiveCash, exchangeRate);
  var rawCashUsd = convertKrwToUsd(rawCash, exchangeRate);
  var operatingCashUsd = convertKrwToUsd(operatingCash, exchangeRate);
  var stockValueUsd = convertKrwToUsd(data.stock_value, exchangeRate);
  var rows = [];
  rows.push(_dualRow('\uCD1D\uC790\uC0B0', formatAmount(totalAssetUsd, 'USD'), formatAmount(data.total_asset, 'KRW'), 'text-white font-medium'));
  rows.push(_dualRow('\uC2E4\uC8FC\uBB38 \uAE30\uC900 \uD604\uAE08', formatAmount(effectiveCashUsd, 'USD'), formatAmount(effectiveCash, 'KRW'), 'text-sky-300 font-medium'));
  if (Math.abs(effectiveCash - rawCash) >= 1)
    rows.push(_dualRow('\uBE0C\uB85C\uCEE4 \uD604\uAE08', formatAmount(rawCashUsd, 'USD'), formatAmount(rawCash, 'KRW'), 'text-gray-300'));
  if (Math.abs(operatingCash - effectiveCash) >= 1)
    rows.push(_dualRow('\uC8FC\uC2DD \uC81C\uC678 \uD604\uAE08', formatAmount(operatingCashUsd, 'USD'), formatAmount(operatingCash, 'KRW'), 'text-gray-300'));
  rows.push(_dualRow('\uC8FC\uC2DD \uD3C9\uAC00', formatAmount(stockValueUsd, 'USD'), formatAmount(data.stock_value, 'KRW'), 'text-gray-200'));
  rows.push(_dualRow('\uC190\uC775', formatSignedAmount(totalPnlRate, 'PCT'), formatSignedAmount(totalPnl, 'KRW'), pnlColor));
  if (shouldShowRawPnl(data))
    rows.push(_dualRow('\uBE0C\uB85C\uCEE4 \uC694\uC57D \uC190\uC775', formatSignedAmount(rawTotalPnlRate, 'PCT'), formatSignedAmount(rawTotalPnl, 'KRW'), 'text-gray-400'));
  rows.push(_noteRow('\uD658\uC728 ' + (exchangeRate ? exchangeRate.toFixed(2) : '-') + ' KRW/USD'));
  if (data.pnl_source === 'HOLDINGS_SUM') rows.push(_noteRow('\uBAA8\uC758\uD22C\uC790 \uC190\uC775\uC740 \uBCF4\uC720\uC885\uBAA9 \uAE30\uC900\uC73C\uB85C \uC7AC\uACC4\uC0B0\uD569\uB2C8\uB2E4.'));
  if (data.status_message) rows.push(_noteRow(data.status_message, 'text-gray-500'));
  rows.forEach(function (r) { el.appendChild(r); });
}

function _renderBalanceKRX(el, data, effectiveCash, rawCash, operatingCash, totalPnl, totalPnlRate, rawTotalPnl, rawTotalPnlRate, pnlColor) {
  var cashLabels = getKrxCashLabels(data);

  // Hero: 총자산
  var hero = document.createElement('div');
  hero.className = 'mb-1';
  var heroLabel = document.createElement('div');
  heroLabel.className = 'text-[11px] text-gray-500';
  heroLabel.textContent = '\uCD1D\uC790\uC0B0';
  var heroValue = document.createElement('div');
  heroValue.className = 'text-sm text-white font-semibold';
  heroValue.textContent = formatKRWFull(data.total_asset);
  hero.appendChild(heroLabel);
  hero.appendChild(heroValue);
  el.appendChild(hero);

  // Key metrics
  var cashText = formatKRWFull(effectiveCash);
  var cashLabel = cashLabels.primary;
  if (effectiveCash > data.total_asset) {
    cashLabel = '\uC608\uC218\uAE08(D+2)';
  } else if (data.total_asset > 0) {
    var cashRatio = (effectiveCash / data.total_asset * 100).toFixed(1);
    cashText += ' (' + cashRatio + '%)';
  }
  el.appendChild(_singleRow(cashLabel, cashText));
  if (Math.abs(effectiveCash - rawCash) >= 1) el.appendChild(_singleRow(cashLabels.secondary, formatKRWFull(rawCash), 'text-gray-500'));
  el.appendChild(_singleRow('\uC6B4\uC601\uAC00\uB2A5 \uD604\uAE08', formatKRWFull(operatingCash), 'text-sky-300'));
  if (data.purchase_amount > 0) el.appendChild(_singleRow('\uB9E4\uC785\uAE08\uC561', formatKRWFull(data.purchase_amount), 'text-gray-500'));
  el.appendChild(_singleRow('\uC8FC\uC2DD\uD3C9\uAC00', formatKRWFull(data.stock_value)));

  // Separator
  var sep = document.createElement('div');
  sep.className = 'border-t border-gray-700/50 my-1';
  el.appendChild(sep);

  // P&L
  var pnlText = (totalPnl >= 0 ? '+' : '') + formatKRWFull(totalPnl) + ' (' + (totalPnlRate >= 0 ? '+' : '') + totalPnlRate.toFixed(2) + '%)';
  el.appendChild(_singleRow('\uC190\uC775', pnlText, pnlColor));
  if (shouldShowRawPnl(data))
    el.appendChild(_singleRow('\uBE0C\uB85C\uCEE4 \uC694\uC57D \uC190\uC775', formatSignedAmount(rawTotalPnl, 'KRW') + ' (' + formatSignedAmount(rawTotalPnlRate, 'PCT') + ')', 'text-gray-500'));

  // Notes
  if (data.cash_source === 'BROKER_ORDERABLE') el.appendChild(_noteRow('\uAC00\uC6A9\uD604\uAE08\uC740 \uBE0C\uB85C\uCEE4 \uC8FC\uBB38\uAC00\uB2A5\uAE08\uC561 \uAE30\uC900\uC785\uB2C8\uB2E4.'));
  if (data.operating_cash != null) el.appendChild(_noteRow('\uC6B4\uC601\uAC00\uB2A5 \uD604\uAE08\uC740 \uCD1D\uC790\uC0B0 - \uC8FC\uC2DD\uD3C9\uAC00 \uAE30\uC900\uC785\uB2C8\uB2E4.'));
  if (data.cash_source === 'TOTAL_ASSET_PROXY') el.appendChild(_noteRow('\uCD1D\uC790\uC0B0 - \uC8FC\uC2DD\uD3C9\uAC00\uC561\uC73C\uB85C \uC8FC\uBB38\uAC00\uB2A5 \uD604\uAE08\uC744 \uCD94\uC815\uD569\uB2C8\uB2E4.'));
  if (data.pnl_source === 'HOLDINGS_SUM') el.appendChild(_noteRow('\uC190\uC775\uC740 \uBCF4\uC720\uC885\uBAA9 \uAE30\uC900\uC73C\uB85C \uC7AC\uACC4\uC0B0\uD569\uB2C8\uB2E4.'));
  if (data.status_message) el.appendChild(_noteRow(data.status_message, 'text-gray-500'));
}

function _singleRow(label, value, valueClass) {
  var row = document.createElement('div');
  row.className = 'flex justify-between';
  var lbl = document.createElement('span');
  lbl.className = 'text-gray-400';
  lbl.textContent = label;
  var val = document.createElement('span');
  val.className = valueClass || '';
  val.textContent = value;
  row.appendChild(lbl);
  row.appendChild(val);
  return row;
}

function _dualRow(label, primary, secondary, primaryClass) {
  var row = document.createElement('div');
  row.className = 'flex justify-between gap-3';
  var lbl = document.createElement('span');
  lbl.className = 'text-gray-500';
  lbl.textContent = label;
  var right = document.createElement('div');
  right.className = 'text-right';
  var p = document.createElement('div');
  p.className = primaryClass || '';
  p.textContent = primary;
  var s = document.createElement('div');
  s.className = 'text-gray-500 text-[11px]';
  s.textContent = secondary;
  right.appendChild(p);
  right.appendChild(s);
  row.appendChild(lbl);
  row.appendChild(right);
  return row;
}

function _noteRow(text, cls) {
  var row = document.createElement('div');
  row.className = cls || 'text-[11px] leading-4 text-sky-300';
  row.textContent = text;
  return row;
}

// ── Holdings ──
export function renderHoldings(data, market) {
  var el = document.getElementById('holdings-info');
  var countEl = document.getElementById('holdings-count');
  var sectionEl = document.getElementById('holdings-section');
  if (!el) return;
  if (!data || !data.length) {
    if (sectionEl) sectionEl.style.display = 'none';
    return;
  }
  if (sectionEl) sectionEl.style.display = '';
  if (countEl) countEl.textContent = data.length + '\uC885\uBAA9';
  var isUS = market !== 'KRX';
  el.replaceChildren();

  // KRX holdings summary
  if (!isUS && data.length > 0) {
    var totalEval = 0;
    var totalHoldingsPnl = 0;
    data.forEach(function (h) {
      totalEval += h.current_price * h.quantity;
      totalHoldingsPnl += Number(h.pnl || 0);
    });
    var totalBuy = totalEval - totalHoldingsPnl;
    var totalHoldingsPnlRate = totalBuy > 0 ? (totalHoldingsPnl / totalBuy) * 100 : 0;
    var summaryPnlColor = totalHoldingsPnl >= 0 ? 'text-green-400' : 'text-red-400';

    var summary = document.createElement('div');
    summary.className = 'holdings-summary';
    var evalRow = document.createElement('div');
    evalRow.className = 'flex justify-between text-xs';
    var evalLabel = document.createElement('span');
    evalLabel.className = 'text-gray-400';
    evalLabel.textContent = '\uD3C9\uAC00\uD569\uACC4';
    var evalValue = document.createElement('span');
    evalValue.className = 'text-white font-medium';
    evalValue.textContent = formatKRWFull(totalEval);
    evalRow.appendChild(evalLabel);
    evalRow.appendChild(evalValue);
    summary.appendChild(evalRow);

    var pnlRow = document.createElement('div');
    pnlRow.className = 'flex justify-between text-xs';
    var pnlLabel = document.createElement('span');
    pnlLabel.className = 'text-gray-400';
    pnlLabel.textContent = '\uC190\uC775\uD569\uACC4';
    var pnlValue = document.createElement('span');
    pnlValue.className = summaryPnlColor + ' font-medium';
    pnlValue.textContent = (totalHoldingsPnl >= 0 ? '+' : '') + formatKRWFull(totalHoldingsPnl) + ' (' + (totalHoldingsPnlRate >= 0 ? '+' : '') + totalHoldingsPnlRate.toFixed(2) + '%)';
    pnlRow.appendChild(pnlLabel);
    pnlRow.appendChild(pnlValue);
    summary.appendChild(pnlRow);

    el.appendChild(summary);
  }

  data.forEach(function (h) {
    var pnlColor = h.pnl_rate >= 0 ? 'text-green-400' : 'text-red-400';
    var currency = h.currency || (isUS ? 'USD' : 'KRW');
    var evalAmt = h.current_price * h.quantity;
    var card = document.createElement('div');
    if (isUS) {
      card.className = 'sidebar-card sidebar-card-us space-y-1';
      _buildUSHoldingCard(card, h, pnlColor, currency, evalAmt);
    } else {
      card.className = 'sidebar-card space-y-0.5';
      _buildKRXHoldingCard(card, h, pnlColor, currency, evalAmt);
    }
    el.appendChild(card);
  });
}

function _buildUSHoldingCard(card, h, pnlColor, currency, evalAmt) {
  var evalAmtKrw = evalAmt * (h.exchange_rate_to_krw || 0);
  var pnlKrw = h.pnl * (h.exchange_rate_to_krw || 0);
  // Row 1: name + pnl rate
  var r1 = document.createElement('div');
  r1.className = 'flex justify-between items-center gap-2';
  var nameBlock = document.createElement('div');
  nameBlock.className = 'min-w-0';
  var nameEl = document.createElement('div');
  nameEl.className = 'text-gray-100 font-medium truncate';
  nameEl.title = h.symbol;
  nameEl.textContent = h.name;
  var subEl = document.createElement('div');
  subEl.className = 'text-[11px] text-gray-500';
  subEl.textContent = h.symbol + ' \u00b7 ' + h.quantity + '\uC8FC';
  nameBlock.appendChild(nameEl);
  nameBlock.appendChild(subEl);
  var rateEl = document.createElement('span');
  rateEl.className = pnlColor + ' font-medium';
  rateEl.textContent = formatSignedAmount(h.pnl_rate, 'PCT');
  r1.appendChild(nameBlock);
  r1.appendChild(rateEl);
  card.appendChild(r1);
  // Row 2: avg + current
  card.appendChild(_flexRow('\uD3C9\uB2E8 ' + formatAmount(h.avg_buy_price, currency), '\uD604\uC7AC ' + formatAmount(h.current_price, currency), 'text-gray-400'));
  // Row 3: eval
  card.appendChild(_flexRow('\uD3C9\uAC00 ' + formatAmount(evalAmt, currency), formatAmount(evalAmtKrw, 'KRW'), 'text-gray-500'));
  // Row 4: pnl
  var r4 = document.createElement('div');
  r4.className = 'flex justify-between text-gray-500';
  var p1 = document.createElement('span');
  p1.className = pnlColor;
  p1.textContent = formatSignedAmount(h.pnl, currency);
  var p2 = document.createElement('span');
  p2.className = pnlColor;
  p2.textContent = formatSignedAmount(pnlKrw, 'KRW');
  r4.appendChild(p1);
  r4.appendChild(p2);
  card.appendChild(r4);
}

function _buildKRXHoldingCard(card, h, pnlColor, currency, evalAmt) {
  // Row 1: name + pnl rate
  var r1 = document.createElement('div');
  r1.className = 'flex justify-between items-center';
  var nameEl = document.createElement('span');
  nameEl.className = 'text-gray-200 font-medium truncate';
  nameEl.title = h.symbol;
  nameEl.textContent = h.name;
  var rateEl = document.createElement('span');
  rateEl.className = pnlColor + ' font-medium';
  rateEl.textContent = (h.pnl_rate >= 0 ? '+' : '') + h.pnl_rate.toFixed(2) + '%';
  r1.appendChild(nameEl);
  r1.appendChild(rateEl);
  card.appendChild(r1);
  // Row 2
  var fmtFull = currency === 'KRW' ? formatKRWFull : function (v) { return formatAmount(v, currency); };
  card.appendChild(_flexRow(h.quantity + '\uC8FC | \uD3C9\uB2E8 ' + fmtFull(h.avg_buy_price), '\uD604\uC7AC ' + fmtFull(h.current_price), 'text-gray-500'));
  // Row 3
  var r3 = document.createElement('div');
  r3.className = 'flex justify-between text-gray-500';
  var evalEl = document.createElement('span');
  evalEl.textContent = '\uD3C9\uAC00 ' + fmtFull(evalAmt);
  if (currency !== 'KRW') {
    var sec = document.createElement('span');
    sec.className = 'text-gray-600 ml-1';
    sec.textContent = formatKRWFull(evalAmt * (h.exchange_rate_to_krw || 0));
    evalEl.appendChild(sec);
  }
  var pnlEl = document.createElement('span');
  pnlEl.className = pnlColor;
  var absPnl = Math.trunc(Math.abs(h.pnl));
  var pnlSign = absPnl === 0 ? '' : (h.pnl >= 0 ? '+' : '-');
  pnlEl.textContent = pnlSign + fmtFull(absPnl);
  if (currency !== 'KRW') {
    var sec2 = document.createElement('span');
    sec2.className = 'text-gray-600 ml-1';
    sec2.textContent = formatSignedAmount(h.pnl * (h.exchange_rate_to_krw || 0), 'KRW');
    pnlEl.appendChild(sec2);
  }
  r3.appendChild(evalEl);
  r3.appendChild(pnlEl);
  card.appendChild(r3);
}

function _flexRow(left, right, cls) {
  var row = document.createElement('div');
  row.className = 'flex justify-between ' + (cls || '');
  var l = document.createElement('span');
  l.textContent = left;
  var r = document.createElement('span');
  r.textContent = right;
  row.appendChild(l);
  row.appendChild(r);
  return row;
}

// ── Pending Orders ──
export function renderPendingOrders(data, market) {
  var el = document.getElementById('pending-orders-info');
  var countEl = document.getElementById('pending-count');
  var sectionEl = document.getElementById('pending-section');
  if (!el) return;
  if (!data || !data.length) {
    if (sectionEl) sectionEl.style.display = 'none';
    return;
  }
  if (sectionEl) sectionEl.style.display = '';
  var isUS = market !== 'KRX';
  var defaultCurrency = isUS ? 'USD' : 'KRW';
  var totalAmt = data.reduce(function (s, o) { return s + o.order_price * o.remaining_qty; }, 0);
  if (countEl) countEl.textContent = data.length + '\uAC74 (' + formatAmount(totalAmt, (data[0] && data[0].currency) || defaultCurrency) + ')';
  el.replaceChildren();
  data.forEach(function (o) {
    var sideColor = o.side === '\uB9E4\uC218' ? 'text-red-400' : 'text-blue-400';
    var currency = o.currency || defaultCurrency;
    var orderAmt = o.order_price * o.remaining_qty;
    var timeStr = o.order_time
      ? o.order_time.slice(0, 2) + ':' + o.order_time.slice(2, 4) + ':' + o.order_time.slice(4, 6) : '';
    var card = document.createElement('div');
    if (isUS) {
      card.className = 'sidebar-card sidebar-card-pending-us space-y-1';
      _buildUSPendingCard(card, o, sideColor, currency, orderAmt, timeStr);
    } else {
      card.className = 'sidebar-card sidebar-card-pending space-y-0.5';
      _buildKRXPendingCard(card, o, sideColor, currency, orderAmt, timeStr);
    }
    el.appendChild(card);
  });
}

function _buildUSPendingCard(card, o, sideColor, currency, orderAmt, timeStr) {
  var orderAmtKrw = orderAmt * (o.exchange_rate_to_krw || 0);
  // Row 1: name + side
  var r1 = document.createElement('div');
  r1.className = 'flex justify-between items-center gap-2';
  var nameBlock = document.createElement('div');
  nameBlock.className = 'min-w-0';
  var nameEl = document.createElement('div');
  nameEl.className = 'text-gray-100 font-medium truncate';
  nameEl.title = o.symbol;
  nameEl.textContent = o.name || o.symbol;
  var subEl = document.createElement('div');
  subEl.className = 'text-[11px] text-gray-500';
  subEl.textContent = o.symbol;
  nameBlock.appendChild(nameEl);
  nameBlock.appendChild(subEl);
  var sideEl = document.createElement('span');
  sideEl.className = sideColor + ' text-xs font-medium';
  sideEl.textContent = o.side;
  r1.appendChild(nameBlock);
  r1.appendChild(sideEl);
  card.appendChild(r1);
  card.appendChild(_flexRow('\uBBF8\uCCB4\uACB0 ' + o.remaining_qty + '\uC8FC / ' + o.order_qty + '\uC8FC', formatAmount(o.order_price, currency), 'text-gray-400'));
  card.appendChild(_flexRow(formatAmount(orderAmt, currency) + ' \u00b7 ' + formatAmount(orderAmtKrw, 'KRW'), timeStr, 'text-gray-500'));
}

function _buildKRXPendingCard(card, o, sideColor, currency, orderAmt, timeStr) {
  // Row 1: name + side badge
  var r1 = document.createElement('div');
  r1.className = 'flex justify-between items-center';
  var nameEl = document.createElement('span');
  nameEl.className = 'text-gray-200 font-medium truncate';
  nameEl.title = o.symbol;
  nameEl.textContent = o.name;
  var sideEl = document.createElement('span');
  sideEl.className = sideColor + ' font-medium text-xs px-1.5 py-0.5 rounded ' + (o.side === '\uB9E4\uC218' ? 'bg-red-900/30' : 'bg-blue-900/30');
  sideEl.textContent = o.side;
  r1.appendChild(nameEl);
  r1.appendChild(sideEl);
  card.appendChild(r1);
  card.appendChild(_flexRow('\uBBF8\uCCB4\uACB0 ' + o.remaining_qty + '\uC8FC / ' + o.order_qty + '\uC8FC', formatAmount(o.order_price, currency), 'text-gray-500'));
  // Row 3: amount + time
  var r3Left = formatAmount(orderAmt, currency);
  var converted = currency !== 'KRW' ? ' ' + formatAmount(orderAmt * (o.exchange_rate_to_krw || 0), 'KRW') : '';
  card.appendChild(_flexRow(r3Left + converted, timeStr, 'text-gray-500'));
}

// ── AI Watchlist ──
export async function loadWatchlist() {
  var market = state.currentMarket;
  var marketParam = market === 'KRX' ? '' : '?market=' + market;
  try {
    var json = await fetchJSON(API + '/watchlist' + marketParam);
    renderWatchlist(json.data);
  } catch (err) {
    console.error('Watchlist load error:', err);
  }
}

function getStreamHealthMeta(stream) {
  var health = String((stream && stream.health) || '').toUpperCase();
  if (health === 'CONNECTED') {
    return {
      dotClass: 'w-1.5 h-1.5 rounded-full bg-green-400 status-dot',
      label: '\uC815\uC0C1',
    };
  }
  if (health === 'DEGRADED') {
    return {
      dotClass: 'w-1.5 h-1.5 rounded-full bg-amber-400 status-dot',
      label: '\uC800\uD558',
    };
  }
  if (health === 'ERROR') {
    return {
      dotClass: 'w-1.5 h-1.5 rounded-full bg-red-400',
      label: '\uC624\uB958',
    };
  }
  return {
    dotClass: 'w-1.5 h-1.5 rounded-full bg-gray-500',
    label: '\uBBF8\uC5F0\uACB0',
  };
}

export function renderWatchlist(data) {
  var el = document.getElementById('watchlist-info');
  var countEl = document.getElementById('watchlist-count');
  var sectionEl = document.getElementById('watchlist-section');
  var dotEl = document.getElementById('watchlist-stream-dot');
  if (!el) return;

  var symbols = (data && data.symbols) || [];
  var stream = data && data.stream_status;

  if (!symbols.length) {
    if (sectionEl) sectionEl.style.display = 'none';
    var tabBadge = document.getElementById('watchlist-tab-count');
    if (tabBadge) tabBadge.style.display = 'none';
    return;
  }

  if (sectionEl) sectionEl.style.display = '';
  if (countEl) countEl.textContent = symbols.length + '\uC885\uBAA9';
  if (dotEl && stream) {
    dotEl.className = getStreamHealthMeta(stream).dotClass;
  }

  el.replaceChildren();

  // WS gauge bar
  if (stream) {
    var healthMeta = getStreamHealthMeta(stream);
    var desiredCount = Number(stream.desired_count || 0);
    var confirmedCount = Number(stream.subscription_count || 0);
    var gaugeBase = desiredCount > 0 ? desiredCount : Number(stream.subscription_limit || 0);
    var bar = document.createElement('div');
    bar.className = 'watchlist-stream-bar';
    var pct = gaugeBase > 0
      ? Math.round((confirmedCount / gaugeBase) * 100) : 0;
    var gaugeTrack = document.createElement('span');
    gaugeTrack.className = 'ws-gauge';
    gaugeTrack.textContent = '';
    // Build gauge using DOM
    var wsLabel = document.createElement('span');
    wsLabel.textContent = 'WS';
    var trackEl = document.createElement('span');
    trackEl.className = 'ws-gauge-track';
    var fillEl = document.createElement('span');
    fillEl.className = 'ws-gauge-fill';
    fillEl.style.width = pct + '%';
    trackEl.appendChild(fillEl);
    var countSpan = document.createElement('span');
    countSpan.textContent = confirmedCount + '/' + desiredCount;
    gaugeTrack.appendChild(wsLabel);
    gaugeTrack.appendChild(trackEl);
    gaugeTrack.appendChild(countSpan);
    var statusSpan = document.createElement('span');
    statusSpan.textContent = healthMeta.label;
    bar.appendChild(gaugeTrack);
    bar.appendChild(statusSpan);
    el.appendChild(bar);

    var statusReason = stream.status_reason || stream.last_business_error || stream.last_connect_error || '';
    if (statusReason) {
      var reasonEl = document.createElement('div');
      reasonEl.className = 'text-[11px] text-gray-500 mt-1';
      reasonEl.textContent = statusReason;
      reasonEl.title = statusReason;
      el.appendChild(reasonEl);
    }
  }

  var isUS = state.currentMarket !== 'KRX';

  symbols.forEach(function (s) {
    var card = document.createElement('div');
    card.className = 'watchlist-card ' + (s.is_holding ? 'wl-holding' : 'wl-watching');

    // Header: symbol + name + badge
    var header = document.createElement('div');
    header.className = 'wl-header';
    var left = document.createElement('div');
    left.className = 'flex items-center gap-1.5 min-w-0';
    var symEl = document.createElement('span');
    symEl.className = 'wl-symbol';
    symEl.textContent = s.symbol;
    left.appendChild(symEl);
    if (s.name) {
      var nameEl = document.createElement('span');
      nameEl.className = 'wl-name';
      nameEl.textContent = s.name;
      nameEl.title = s.name;
      left.appendChild(nameEl);
    }
    var badge = document.createElement('span');
    if (s.is_holding) {
      badge.className = 'wl-badge wl-badge-holding';
      badge.textContent = '\uBCF4\uC720';
    } else if (!s.is_subscribed) {
      badge.className = 'wl-badge wl-badge-nosub';
      badge.textContent = '\uB300\uAE30';
    } else {
      badge.className = 'wl-badge wl-badge-watching';
      badge.textContent = '\uAC10\uC2DC';
    }
    header.appendChild(left);
    header.appendChild(badge);
    card.appendChild(header);

    // Holding P&L info (보유종목 전용)
    if (s.holding_info) {
      var hi = s.holding_info;
      var hiPnlColor = hi.pnl_rate >= 0 ? 'text-green-400' : 'text-red-400';
      var info = document.createElement('div');
      info.className = 'wl-holding-info';
      var r1 = document.createElement('div');
      r1.className = 'flex justify-between';
      var rateEl2 = document.createElement('span');
      rateEl2.className = hiPnlColor + ' font-medium';
      rateEl2.textContent = (hi.pnl_rate >= 0 ? '+' : '') + hi.pnl_rate.toFixed(2) + '%';
      var evalEl2 = document.createElement('span');
      evalEl2.textContent = formatKRWFull(hi.eval_amount);
      r1.appendChild(rateEl2);
      r1.appendChild(evalEl2);
      info.appendChild(r1);
      var r2 = document.createElement('div');
      r2.className = 'flex justify-between text-gray-500';
      var curEl = document.createElement('span');
      curEl.textContent = '\uD604\uC7AC ' + formatKRWFull(hi.current_price);
      var avgEl = document.createElement('span');
      avgEl.textContent = '\uD3C9\uB2E8 ' + formatKRWFull(hi.avg_buy_price);
      r2.appendChild(curEl);
      r2.appendChild(avgEl);
      info.appendChild(r2);
      card.appendChild(info);
    }

    // Thresholds grid
    var th = s.thresholds;
    if (th) {
      var grid = document.createElement('div');
      grid.className = 'wl-thresholds';
      var fmtPrice = function (v) {
        if (!v || v <= 0) return '-';
        return isUS ? '$' + Number(v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
          : Number(v).toLocaleString('ko-KR') + '\uC6D0';
      };
      var items = [];
      if (th.surge_pct != null) items.push({ label: '\uAE09\uB4F1', value: '+' + th.surge_pct + '%', cls: 'wl-th-val-surge' });
      if (th.drop_pct != null) items.push({ label: '\uAE09\uB77D', value: th.drop_pct + '%', cls: 'wl-th-val-drop' });
      if (th.volume_spike_ratio) items.push({ label: '\uAC70\uB798\uB7C9', value: 'x' + th.volume_spike_ratio, cls: '' });
      if (th.trailing_stop_pct > 0) items.push({ label: 'Trail', value: th.trailing_stop_pct + '%', cls: '' });
      if (th.stop_loss > 0) items.push({ label: 'SL', value: fmtPrice(th.stop_loss), cls: 'wl-th-val-sl' });
      if (th.take_profit > 0) items.push({ label: 'TP', value: fmtPrice(th.take_profit), cls: 'wl-th-val-tp' });
      items.forEach(function (item) {
        var cell = document.createElement('div');
        cell.className = 'wl-th';
        var lbl = document.createElement('span');
        lbl.className = 'wl-th-label';
        lbl.textContent = item.label;
        var val = document.createElement('span');
        val.className = item.cls;
        val.textContent = item.value;
        cell.appendChild(lbl);
        cell.appendChild(val);
        grid.appendChild(cell);
      });
      card.appendChild(grid);
    } else {
      var tag = document.createElement('div');
      tag.className = 'wl-default-tag';
      tag.textContent = '\uAE30\uBCF8 \uC784\uACC4\uAC12';
      card.appendChild(tag);
    }

    el.appendChild(card);
  });

  // Update tab badge
  var tabBadge2 = document.getElementById('watchlist-tab-count');
  if (tabBadge2) {
    tabBadge2.textContent = symbols.length;
    tabBadge2.style.display = '';
  }

  refreshIcons();
}
