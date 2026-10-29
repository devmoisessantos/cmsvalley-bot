"""
Atualiza o painel fixo de plantão ativo a cada minuto.

Busca quem tem toggle ligado, monta o card e edita a mesma mensagem
no canal PAINEL_FIXO_PLANTAO_ATIVO. Se a mensagem sumiu, publica de novo.

Regra always-on:
  a cada 60 s → um único edit (lista, tempos, status, rodapé <t:R>).
  O rodapé recomeça em "há 1 segundo" e sobe até ~1 minuto.

Proteções:
  - trava (lock) evita dois edits ao mesmo tempo
  - debounce: se o último edit foi há menos de 50 s, pula o ciclo
    (cobre instância duplicada ou loop acionado em dobro)
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
# Não edita de novo se o ciclo anterior terminou há menos que isso.
MINIMO_ENTRE_EDITS = 50.0


class PlantaoAtivosTasks(commands.Cog):
    """
    Cog só de tarefa: mantém o painel de plantão ativo atualizado.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._trava_atualizacao = asyncio.Lock()
        self._ultimo_edit_monotonic = 0.0
        # Evita start() em dobro se o cog for adicionado de novo.
        if not self.atualizar_painel_ativos.is_running():
            self.atualizar_painel_ativos.start()

    def cog_unload(self) -> None:
        self.atualizar_painel_ativos.cancel()

    @tasks.loop(seconds=INTERVALO_SEGUNDOS)
    async def atualizar_painel_ativos(self) -> None:
        """Um edit por ciclo de 60 s (com trava e debounce)."""
        if self._trava_atualizacao.locked():
            registrador.debug(
                "Plantão ativo: ciclo anterior ainda em andamento; pulando."
            )
            return

        async with self._trava_atualizacao:
            decorrido = time.monotonic() - self._ultimo_edit_monotonic
            if (
                self._ultimo_edit_monotonic > 0
                and decorrido < MINIMO_ENTRE_EDITS
            ):
                registrador.debug(
                    "Plantão ativo: debounce (último edit há %.0f s); pulando.",
                    decorrido,
                )
                return

            try:
                await sincronizar_relogio_com_rede()
                await self._atualizar_ou_publicar()
                self._ultimo_edit_monotonic = time.monotonic()
            except Exception as erro_capturado:
                registrador.exception(
                    "Falha ao atualizar o painel de plantão ativo: %s",
                    erro_capturado,
                )

    @atualizar_painel_ativos.before_loop
    async def _esperar_bot_pronto(self) -> None:
        await self.bot.wait_until_ready()

    async def _atualizar_ou_publicar(self) -> None:
        canal_id = CANAIS.get("PAINEL_FIXO_PLANTAO_ATIVO") or 0
        if not canal_id:
            registrador.debug(
                "PAINEL_FIXO_PLANTAO_ATIVO não configurado; pulando."
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
            await mensagem.edit(view=view)
            registrador.info(
                "Plantão ativo atualizado (edit message_id=%s).",
                message_id,
            )
            return True
        except discord.HTTPException as erro_edicao:
            codigo = getattr(erro_edicao, "status", None)
            if codigo == 429:
                registrador.debug(
                    "Rate limit ao editar plantão ativo %s; tenta de novo.",
                    message_id,
                )
                # Conta como "tentou": evita martelar no próximo ciclo imediato.
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
    # Se o cog já existir (reload), remove antes de adicionar de novo.
    nome_do_cog = PlantaoAtivosTasks.__name__
    if bot.get_cog(nome_do_cog) is not None:
        await bot.remove_cog(nome_do_cog)
    await bot.add_cog(PlantaoAtivosTasks(bot))
