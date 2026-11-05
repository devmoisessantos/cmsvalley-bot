"""
Cog: liga o bot ao Dashboard web (cmsvalley-api).

Responsabilidades:
  1. HEARTBEAT — avisa à API que o bot está online (latência, guilds, membros,
     cogs). O painel mostra online/offline com isso.
  2. FILA DE COMANDOS — o site grava comandos (embed, anúncio, sync de metas)
     e este cog consome e aplica no Discord.

Configuração (.env do bot):
  DASHBOARD_API_URL     ex.: https://ems-ocr-api.onrender.com  (sem / no fim)
  DASHBOARD_API_TOKEN   o MESMO valor do JWT_SECRET da API do dashboard
  DASHBOARD_CANAL_PADRAO  ID do canal onde embeds/metas são postados (opcional)

Sem essas variáveis o cog carrega, mas fica inativo (só loga um aviso).
"""

from __future__ import annotations

import logging
import os

import aiohttp
import discord
from discord.ext import commands, tasks

from src.config import (
    CANAIS,
    DASHBOARD_API_TOKEN,
    DASHBOARD_API_URL,
    DASHBOARD_CANAL_PADRAO,
)

registrador = logging.getLogger(__name__)

VERSAO_BOT = "cmsvalley-bot 2.4.0"


class DashboardCog(commands.Cog):
    """Heartbeat + consumo da fila de comandos do dashboard web."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._sessao_http: aiohttp.ClientSession | None = None
        self._ativo = bool(DASHBOARD_API_URL and DASHBOARD_API_TOKEN)

    async def cog_load(self) -> None:
        if not self._ativo:
            registrador.warning(
                "DASHBOARD_API_URL/DASHBOARD_API_TOKEN ausentes: "
                "integração com o dashboard desativada."
            )
            return
        self._sessao_http = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=20),
            headers={"Authorization": f"Bearer {DASHBOARD_API_TOKEN}"},
        )
        self.heartbeat.start()
        self.consumir_fila.start()
        registrador.info(
            "Dashboard ativo → %s (canal padrão: %s)",
            DASHBOARD_API_URL,
            self._id_canal_padrao() or "não configurado",
        )
        # Primeiro heartbeat logo após ficar pronto (não espera 1 min).
        # O before_loop do task já espera wait_until_ready.

    async def cog_unload(self) -> None:
        if self.heartbeat.is_running():
            self.heartbeat.cancel()
        if self.consumir_fila.is_running():
            self.consumir_fila.cancel()
        if self._sessao_http and not self._sessao_http.closed:
            await self._sessao_http.close()

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    async def _post(self, caminho: str, corpo: dict) -> dict | None:
        if not self._sessao_http:
            return None
        try:
            async with self._sessao_http.post(
                f"{DASHBOARD_API_URL}{caminho}", json=corpo
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

    async def _get(self, caminho: str) -> dict | list | None:
        if not self._sessao_http:
            return None
        try:
            async with self._sessao_http.get(
                f"{DASHBOARD_API_URL}{caminho}"
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


    # ------------------------------------------------------------------
    # 0) Snapshot real do servidor (cargos, canais, guild)
    # ------------------------------------------------------------------

    def _snapshot_guild(self) -> tuple[list[dict], list[dict], dict]:
        """Cargos e canais REAIS (nome, id, cor, posição) do servidor."""
        guild = None
        bruto = getattr(DASHBOARD_CANAL_PADRAO, "__self__", None)
        if bruto is not None:
            guild = bruto.guild
        if guild is None:
            alvo = (os.environ.get("GUILD_ID") or "").strip()
            guild = self.bot.get_guild(int(alvo)) if alvo.isdigit() else (
                self.bot.guilds[0] if self.bot.guilds else None
            )
        if guild is None:
            return [], [], {}

        cargos = [
            {
                "id": str(c.id),
                "name": c.name,
                "color": f"#{c.colour.value:06x}" if c.colour.value else "",
                "position": c.position,
                "hoist": bool(c.hoist),
                "mentionable": bool(c.mentionable),
                "managed": bool(c.managed),
                "memberCount": len(c.members),
            }
            for c in guild.roles
        ]
        canais = [
            {
                "id": str(c.id),
                "name": c.name,
                "type": int(c.type),
                "position": c.position,
                "parent_id": str(c.category_id) if c.category_id else None,
            }
            for c in guild.channels
        ]
        info = {
            "id": str(guild.id),
            "name": guild.name,
            "icon": guild.icon.key if guild.icon else None,
            "memberCount": guild.member_count or len(guild.members),
            "description": guild.description or "",
        }
        return cargos, canais, info

    # ------------------------------------------------------------------
    # 1) Heartbeat (1 min)
    # ------------------------------------------------------------------

    @tasks.loop(minutes=1)
    async def heartbeat(self) -> None:
        latencia = int((self.bot.latency or 0) * 1000)
        total_membros = sum(g.member_count or 0 for g in self.bot.guilds)
        corpo = {
            "latencia_ms": latencia,
            "guilds": len(self.bot.guilds),
            "membros": total_membros,
            "cogs": len(self.bot.cogs),
            "versao": VERSAO_BOT,
        }
        # Snapshot real de cargos/canais: o dashboard usa se a REST do Discord
        # falhar (nada de cargo fictício no painel em nenhuma hipótese).
        try:
            cargos, canais, info = self._snapshot_guild()
            if cargos:
                corpo["cargos"] = cargos
                corpo["canais"] = canais
                corpo["guild_info"] = info
        except Exception as falha:  # noqa: BLE001
            registrador.debug("snapshot de guild indisponível: %s", falha)
        resposta = await self._post("/internal/heartbeat", corpo)
        if resposta is None:
            registrador.warning(
                "Heartbeat do dashboard falhou (URL/token ou API fora do ar). "
                "Confira DASHBOARD_API_URL e se DASHBOARD_API_TOKEN = JWT_SECRET "
                "da API."
            )

    @heartbeat.before_loop
    async def _esperar_pronto_heartbeat(self) -> None:
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------------
    # 2) Fila de comandos do site (15 s)
    # ------------------------------------------------------------------

    @tasks.loop(seconds=15)
    async def consumir_fila(self) -> None:
        comandos = await self._get("/internal/commands?limit=5")
        if not comandos or not isinstance(comandos, list):
            return
        for comando in comandos:
            await self._executar(comando)

    @consumir_fila.before_loop
    async def _esperar_pronto_fila(self) -> None:
        await self.bot.wait_until_ready()

    async def _executar(self, comando: dict) -> None:
        tipo = comando.get("tipo")
        payload = comando.get("payload") or {}
        comando_id = comando.get("id")
        try:
            if tipo == "send_embed":
                await self._enviar_embed(payload)
                resultado = "embed enviado"
            elif tipo == "announce":
                await self._enviar_anuncio(payload)
                resultado = "anúncio enviado"
            elif tipo in ("sync_now", "goals_changed"):
                await self._publicar_metas()
                resultado = (
                    "painel de metas sincronizado"
                    if tipo == "sync_now"
                    else "metas atualizadas no painel"
                )
            elif tipo == "settings_changed":
                resultado = "configurações recebidas"
            else:
                resultado = f"tipo desconhecido: {tipo}"
            if comando_id is not None:
                await self._ack(int(comando_id), resultado, erro=False)
        except Exception as falha:  # noqa: BLE001
            registrador.exception("Falha ao executar comando dashboard #%s", comando_id)
            if comando_id is not None:
                await self._ack(int(comando_id), str(falha), erro=True)

    async def _ack(self, comando_id: int, resultado: str, erro: bool) -> None:
        await self._post(
            f"/internal/commands/{comando_id}/ack",
            {"resultado": resultado, "erro": erro},
        )

    # ------------------------------------------------------------------
    # Aplicação no Discord
    # ------------------------------------------------------------------

    def _id_canal_padrao(self) -> int:
        if DASHBOARD_CANAL_PADRAO:
            return int(DASHBOARD_CANAL_PADRAO)
        return int(CANAIS.get("CANAL_DASHBOARD") or 0)

    def _resolver_canal(self, payload: dict):
        bruto = payload.get("channel_id") or self._id_canal_padrao()
        if not bruto:
            return None
        try:
            canal_id = int(bruto)
        except (TypeError, ValueError):
            return None
        canal = self.bot.get_channel(canal_id)
        return canal

    async def _enviar_embed(self, payload: dict) -> None:
        canal = self._resolver_canal(payload)
        if canal is None:
            raise RuntimeError(
                "canal não encontrado para embed "
                "(defina DASHBOARD_CANAL_PADRAO ou channel_id no payload)"
            )
        cor_texto = payload.get("color") or "#2ee6c5"
        try:
            cor = discord.Color.from_str(cor_texto)
        except (ValueError, TypeError):
            cor = discord.Color.teal()
        embed = discord.Embed(
            title=payload.get("title") or "Dashboard CMS Valley",
            description=payload.get("description") or "",
            color=cor,
        )
        for campo in payload.get("fields") or []:
            embed.add_field(
                name=campo.get("name") or "—",
                value=campo.get("value") or "—",
                inline=bool(campo.get("inline", False)),
            )
        if payload.get("footer"):
            embed.set_footer(text=str(payload["footer"]))
        await canal.send(embed=embed)

    async def _enviar_anuncio(self, payload: dict) -> None:
        canal = self._resolver_canal(payload)
        if canal is None:
            raise RuntimeError("canal não encontrado para anúncio")
        conteudo = payload.get("content") or ""
        if not conteudo.strip():
            raise RuntimeError("anúncio sem conteúdo")
        await canal.send(conteudo)

    async def _publicar_metas(self) -> None:
        """Busca progresso das metas na API e publica embed no canal do dashboard."""
        dados = await self._get("/internal/overview?period=month")
        if not dados or not isinstance(dados, dict):
            raise RuntimeError("overview indisponível na API do dashboard")

        canal = self._resolver_canal(
            {"channel_id": dados.get("goals_channel_id") or ""}
        )
        if canal is None:
            registrador.info(
                "Canal do dashboard não configurado; metas não postadas. "
                "Defina DASHBOARD_CANAL_PADRAO no .env."
            )
            return

        embed = discord.Embed(
            title="🎯 Metas do mês — dashboard",
            color=discord.Color.from_str("#2ee6c5"),
        )
        metas = dados.get("goals") or []
        if not metas:
            embed.description = "Nenhuma meta cadastrada no dashboard."
        for meta in metas:
            try:
                pct = int(meta.get("pct") or 0)
            except (TypeError, ValueError):
                pct = 0
            pct = max(0, min(100, pct))
            preenchidos = pct // 10
            barra = "█" * preenchidos + "░" * (10 - preenchidos)
            embed.add_field(
                name=str(meta.get("label") or meta.get("chave") or "Meta"),
                value=(
                    f"`{barra}` {pct}% · "
                    f"{meta.get('value', '—')} / {meta.get('target', '—')}"
                ),
                inline=False,
            )
        ao_vivo = dados.get("live_now")
        if ao_vivo is not None:
            embed.set_footer(
                text=f"CMS Valley · dashboard · {ao_vivo} em plantão agora"
            )
        else:
            embed.set_footer(text="CMS Valley · dashboard operacional")
        await canal.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DashboardCog(bot))
