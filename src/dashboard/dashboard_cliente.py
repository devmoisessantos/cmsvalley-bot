"""
Cliente HTTP do bot para a API do dashboard (cmsvalley-api).

Todas as chamadas internas (/internal/*) passam por aqui. O token é o
mesmo JWT_SECRET da API (variável DASHBOARD_API_TOKEN no bot).

"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from src.config import DASHBOARD_API_TOKEN, DASHBOARD_API_URL

registrador = logging.getLogger(__name__)


class ClienteDashboard:
    """Sessão HTTP reutilizável para falar com a API do dashboard."""

    def __init__(self) -> None:
        self._sessao: aiohttp.ClientSession | None = None
        self.ativo = bool(DASHBOARD_API_URL and DASHBOARD_API_TOKEN)

    async def abrir(self) -> None:
        """Cria a sessão HTTP se a integração estiver configurada."""
        if not self.ativo:
            registrador.warning(
                "DASHBOARD_API_URL/DASHBOARD_API_TOKEN ausentes: "
                "integração com o dashboard desativada."
            )
            return
        if self._sessao and not self._sessao.closed:
            return
        self._sessao = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=20),
            headers={"Authorization": f"Bearer {DASHBOARD_API_TOKEN}"},
        )
        registrador.info("Cliente do dashboard aberto → %s", DASHBOARD_API_URL)

    async def fechar(self) -> None:
        """Fecha a sessão HTTP com segurança."""
        if self._sessao and not self._sessao.closed:
            await self._sessao.close()
        self._sessao = None

    async def post(self, caminho: str, corpo: dict) -> dict | None:
        """POST JSON em /internal/* e devolve o corpo ou None em falha."""
        if not self._sessao:
            return None
        try:
            async with self._sessao.post(
                f"{DASHBOARD_API_URL}{caminho}",
                json=corpo,
            ) as resposta:
                if resposta.status >= 400:
                    texto = await resposta.text()
                    registrador.warning(
                        "dashboard POST %s → %s: %s",
                        caminho,
                        resposta.status,
                        texto[:200],
                    )
                    return None
                return await resposta.json(content_type=None)
        except Exception as falha:  # noqa: BLE001
            registrador.warning("dashboard POST %s falhou: %s", caminho, falha)
            return None

    async def get(self, caminho: str) -> dict | list | None:
        """GET JSON em /internal/* e devolve o corpo ou None em falha."""
        if not self._sessao:
            return None
        try:
            async with self._sessao.get(
                f"{DASHBOARD_API_URL}{caminho}",
            ) as resposta:
                if resposta.status >= 400:
                    texto = await resposta.text()
                    registrador.warning(
                        "dashboard GET %s → %s: %s",
                        caminho,
                        resposta.status,
                        texto[:200],
                    )
                    return None
                return await resposta.json(content_type=None)
        except Exception as falha:  # noqa: BLE001
            registrador.warning("dashboard GET %s falhou: %s", caminho, falha)
            return None

    async def enviar_sinal_do_bot(self, corpo: dict) -> dict | None:
        """Registra o sinal do bot na API (online, latência, snapshot)."""
        return await self.post("/internal/sinal-do-bot", corpo)

    async def buscar_comandos(self, limite: int = 5) -> list[dict]:
        """Puxa a fila de comandos pendentes do site."""
        bruto = await self.get(f"/internal/comandos?limit={limite}")
        if not bruto or not isinstance(bruto, list):
            return []
        return bruto

    async def confirmar_comando(
        self,
        comando_id: int,
        resultado: str,
        erro: bool,
    ) -> None:
        """Marca um comando da fila como executado (ou falhou)."""
        await self.post(
            f"/internal/comandos/{comando_id}/ack",
            {"resultado": resultado, "erro": erro},
        )

    async def buscar_overview(self, periodo: str = "month") -> dict[str, Any]:
        """Resumo de metas e KPIs para o painel Discord."""
        bruto = await self.get(f"/internal/visao-geral?period={periodo}")
        if not bruto or not isinstance(bruto, dict):
            return {}
        return bruto

    async def buscar_metas_por_cargo(self) -> list[dict]:
        """Lista metas de produção por cargo vindas do dashboard."""
        bruto = await self.get("/internal/metas-por-cargo")
        if not bruto or not isinstance(bruto, dict):
            return []
        dados = bruto.get("data")
        if not isinstance(dados, list):
            return []
        return dados
