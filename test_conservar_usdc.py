"""Modo «Conservar USDC»: el bot no gasta USDC, solo rota entre activos en ganancia,
y nunca vende ni compra en perdida."""
import os
import unittest
from unittest.mock import patch

import crypto_auto as A

SNAP = {"total_usd": 60.0, "assets": {
    "USDC": {"amount": 20.0, "native_usd": 20.0},
    "ETH": {"amount": 0.004, "native_usd": 10.0},
    "SOL": {"amount": 0.1, "native_usd": 12.0},
    "BTC": {"amount": 0.0001, "native_usd": 8.0},
    "DOT": {"amount": 1.0, "native_usd": 1.2},
}}
GANANCIA = {"ETH": 0.06, "SOL": 0.03, "BTC": 0.20, "DOT": 0.50}


def ps_falso(sym, *_a, **_k):
    return {"known": True, "profit_pct": GANANCIA.get(sym, -0.05)}


class ConservarUsdc(unittest.TestCase):
    def env(self, **extra):
        base = {"ACCUMULATE_USDC": "YES", "NO_SELL_SYMBOLS": "BTC", "NEVER_SELL_AT_LOSS": "YES"}
        base.update(extra)
        return patch.dict(os.environ, base, clear=False)

    def test_bloquea_compras_automaticas_con_usdc(self):
        with self.env():
            self.assertIsNotNone(A.usdc_spend_reason({"action": "BUY", "source": "USDC", "origin": "groq"}))
            self.assertIsNotNone(A.usdc_spend_reason({"action": "BUY", "origin": "conditional_order", "conditional_order_id": "dca-BTC"}))

    def test_tus_ordenes_manuales_si_usan_usdc(self):
        with self.env():
            self.assertIsNone(A.usdc_spend_reason({"action": "BUY", "source": "USDC", "origin": "conditional_order", "conditional_order_id": "co_abc"}))

    def test_ventas_y_rotaciones_no_las_frena_este_modo(self):
        with self.env():
            self.assertIsNone(A.usdc_spend_reason({"action": "SELL", "source": "ETH"}))
            self.assertIsNone(A.usdc_spend_reason({"action": "BUY", "source": "ETH"}))

    def test_apagado_no_cambia_nada(self):
        with self.env(ACCUMULATE_USDC="NO"):
            self.assertIsNone(A.usdc_spend_reason({"action": "BUY", "source": "USDC"}))

    def test_rota_desde_la_de_mayor_ganancia_sin_tocar_btc_ni_usdc(self):
        with self.env(), patch.object(A, "profit_state", ps_falso), patch.object(A, "decision_context", lambda: {}):
            fuente, cant, ps = A.rotation_source("LINK", SNAP, {}, 12.0)
        # BTC (+20 %) esta protegido y DOT (+50 %) vale menos del minimo de 5 USD: gana ETH (+6 %).
        self.assertEqual(fuente, "ETH")
        self.assertAlmostEqual(cant, 0.004)
        self.assertGreater(ps["profit_pct"], 0)

    def test_sin_ganancia_suficiente_no_rota(self):
        perdidas = lambda sym, *_a, **_k: {"known": True, "profit_pct": 0.004}
        with self.env(), patch.object(A, "profit_state", perdidas), patch.object(A, "decision_context", lambda: {}):
            self.assertEqual(A.rotation_source("LINK", SNAP, {}, 12.0)[0], None)

    def test_costo_desconocido_no_rota(self):
        desconocido = lambda sym, *_a, **_k: {"known": False, "profit_pct": 0.5}
        with self.env(), patch.object(A, "profit_state", desconocido), patch.object(A, "decision_context", lambda: {}):
            self.assertEqual(A.rotation_source("LINK", SNAP, {}, 12.0)[0], None)

    def test_no_paga_con_la_misma_moneda_que_compra(self):
        with self.env(), patch.object(A, "profit_state", ps_falso), patch.object(A, "decision_context", lambda: {}):
            self.assertEqual(A.rotation_source("ETH", SNAP, {}, 12.0)[0], "SOL")

    def test_monto_limitado_al_tope_por_operacion(self):
        with self.env(), patch.object(A, "profit_state", ps_falso), patch.object(A, "decision_context", lambda: {}):
            fuente, cant, _ = A.rotation_source("LINK", SNAP, {}, 5.0)
        self.assertEqual(fuente, "ETH")
        self.assertAlmostEqual(cant, 0.004 * 5.0 / 10.0)

    def test_el_candado_de_perdidas_cubre_la_rotacion(self):
        # Pagar con ETH que va en perdida = venderlo con perdida: bloqueado.
        en_perdida = lambda sym, *_a, **_k: {"known": True, "profit_pct": -0.02}
        with self.env(), patch.object(A, "profit_state", en_perdida), patch.object(A, "decision_context", lambda: {}), \
                patch.object(A, "portfolio_snapshot", lambda items: SNAP):
            motivo, _, vendida = A.loss_sell_reason("LINK", {"action": "BUY", "source": "ETH", "origin": "groq"}, {})
        self.assertEqual(vendida, "ETH")
        self.assertIn("nunca se vende con perdida", motivo)


if __name__ == "__main__":
    unittest.main()
