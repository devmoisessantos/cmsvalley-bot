"""
Atualiza o painel fixo de plantão ativo a cada 60 segundos.

A cada ciclo:
  1. Busca quem tem toggle ligado
  2. Recalcula o tempo de cada um (logs fechados + segmento aberto)
  3. Reedita a mesma mensagem (lista, tempos, status, rodapé <t:R>)

O rodapé recomeça em "há 1 segundo". Os cronômetros da lista
avançam conforme o tempo real desde o segmento_iniciado_em.
"""

from __future__ import annotations

import asyncio
import logging
import time

import discord
from discord.ext import commands, tasks
from sqlalchemy import select

from src.config import CANAIS
from src.database.conexao import async_session
from src.database.models import PainelPostado
from src.plantao.plantao_ativos_panel import montar_painel_plantao_ativo
from src.plantao.plantao_service import listar_em_servico
from src.utils.relogio import sincronizar_relogio_com_rede

registrador = logging.getLogger(__name__)

NOME_PAINEL = "plantao_ativo"
INTERVALO_SEGUNDOS = 60


class PlantaoAtivosTasks(commands.Cog):
    """Cog só de tarefa: painel de plantão ativo a cada 60 s."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._trava_atualizacao = asyncio.Lock()
        self._numero_do_ciclo = 0
        if not self.atualizar_painel_ativos.is_running():
            self.atualizar_painel_ativos.start()

    def cog_unload(self) -> None:
        self.atualizar_painel_ativos.cancel()

    @tasks.loop(seconds=INTERVALO_SEGUNDOS)
    async def atualizar_painel_ativos(self) -> None:
        """Um edit por ciclo: lista, tempos e rodapé."""
        if self._trava_atualizacao.locked():
            registrador.warning(
                "Plantão ativo: ciclo pulado (ainda processando o anterior)."
            )
            return

        async with self._trava_atualizacao:
            self._numero_do_ciclo += 1
            numero = self._numero_do_ciclo
            inicio = time.monotonic()
            try:
                await sincronizar_relogio_com_rede()
                await self._atualizar_ou_publicar(numero)
            except Exception as erro_capturado:
                registrador.exception(
                    "Plantão ativo ciclo %s falhou: %s",
                    numero,
                    erro_capturado,
                )
            else:
                decorrido = time.monotonic() - inicio
                registrador.debug(
                    "Plantão ativo ciclo %s terminou em %.1f s.",
                    numero,
                    decorrido,
                )

    @atualizar_painel_ativos.before_loop
    async def _esperar_bot_pronto(self) -> None:
        await self.bot.wait_until_ready()
        registrador.info(
            "Loop plantão ativo iniciado (a cada %s s).",
            INTERVALO_SEGUNDOS,
        )

    async def _atualizar_ou_publicar(self, numero_do_ciclo: int) -> None:
        canal_id = CANAIS.get("PAINEL_FIXO_PLANTAO_ATIVO") or 0
        if not canal_id:
            registrador.warning(
                "PAINEL_FIXO_PLANTAO_ATIVO não configurado; ciclo %s pulado.",
                numero_do_ciclo,
            )
            return

        canal = self.bot.get_channel(canal_id)
        if canal is None:
            try:
                canal = await self.bot.fetch_channel(canal_id)
            except Exception as erro_canal:
                registrador.warning(
                    "Não encontrei o canal de plantão ativo %s: %s",
                    canal_id,
                    erro_canal,
                )
                return

        estados = await listar_em_servico(limite=80)
        guilda = getattr(canal, "guild", None)
        view = await montar_painel_plantao_ativo(estados, guild=guilda)

        registro = await self._buscar_registro()
        if registro is not None:
            atualizado = await self._editar_mensagem_existente(
                canal,
                registro.message_id,
                view,
                numero_do_ciclo,
                len(estados),
            )
            if atualizado:
                return
            await self._publicar_nova_mensagem(canal, view, registro)
            return

        await self._publicar_nova_mensagem(canal, view, None)

    async def _buscar_registro(self):
        async with async_session() as sessao:
            resultado = await sessao.execute(
                select(PainelPostado).where(
                    PainelPostado.nome_painel == NOME_PAINEL
                )
            )
            return resultado.scalar_one_or_none()

    async def _editar_mensagem_existente(
        self,
        canal: discord.abc.Messageable,
        message_id: int,
        view: discord.ui.LayoutView,
        numero_do_ciclo: int,
        quantidade_em_servico: int,
    ) -> bool:
        try:
            mensagem = await canal.fetch_message(message_id)
        except discord.NotFound:
            registrador.info(
                "Mensagem de plantão ativo %s sumiu; vou republicar.",
                message_id,
            )
            return False
        except discord.HTTPException as erro_http:
            registrador.warning(
                "Falha ao buscar mensagem de plantão ativo %s: %s",
                message_id,
                erro_http,
            )
            return False

        try:
            # LayoutView (Components V2): limpa content/embeds para o
            # Discord aceitar só os componentes novos (tempos + rodapé).
            await mensagem.edit(
                content=None,
                embeds=[],
                view=view,
            )
            registrador.info(
                "Plantão ativo ciclo %s: edit ok "
                "(message_id=%s, em_servico=%s).",
                numero_do_ciclo,
                message_id,
                quantidade_em_servico,
            )
            return True
        except discord.HTTPException as erro_edicao:
            codigo = getattr(erro_edicao, "status", None)
            if codigo == 429:
                registrador.warning(
                    "Plantão ativo ciclo %s: rate limit 429 "
                    "(message_id=%s). Tempos NÃO atualizados; "
                    "tenta de novo no próximo ciclo.",
                    numero_do_ciclo,
                    message_id,
                )
                # True = não republicar em cima do rate limit.
                return True
            registrador.warning(
                "Falha ao editar plantão ativo %s: %s. Republicando.",
                message_id,
                erro_edicao,
            )
            try:
                await mensagem.delete()
            except discord.HTTPException:
                pass
            return False

    async def _publicar_nova_mensagem(
        self,
        canal: discord.abc.Messageable,
        view: discord.ui.LayoutView,
        registro_antigo,
    ) -> None:
        mensagem = await canal.send(view=view)

        async with async_session() as sessao:
            if registro_antigo is not None:
                resultado = await sessao.execute(
                    select(PainelPostado).where(
                        PainelPostado.nome_painel == NOME_PAINEL
                    )
                )
                registro = resultado.scalar_one_or_none()
                if registro is not None:
                    registro.message_id = mensagem.id
                    registro.canal_id = canal.id
                    sessao.add(registro)
                else:
                    sessao.add(
                        PainelPostado(
                            nome_painel=NOME_PAINEL,
                            canal_id=canal.id,
                            message_id=mensagem.id,
                        )
                    )
            else:
                sessao.add(
                    PainelPostado(
                        nome_painel=NOME_PAINEL,
                        canal_id=canal.id,
                        message_id=mensagem.id,
                    )
                )
            await sessao.commit()

        registrador.info(
            "Painel de plantão ativo publicado (message_id=%s).",
            mensagem.id,
        )


async def setup(bot: commands.Bot) -> None:
    nome_do_cog = PlantaoAtivosTasks.__name__
    if bot.get_cog(nome_do_cog) is not None:
        await bot.remove_cog(nome_do_cog)
    await bot.add_cog(PlantaoAtivosTasks(bot))
