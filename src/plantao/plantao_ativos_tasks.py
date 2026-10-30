"""
Atualiza o painel fixo de plantão ativo a cada 60 segundos.

Loop próprio (asyncio), sem discord.ext.tasks — o tasks.loop estava
disparando o corpo em dobro a cada ~2 min (ciclo N e N+1 com 1 s de
diferença). Um único ``create_task`` + ``sleep`` garante:

  edit → espera 60 s → edit → espera 60 s → …

A cada edit: lista, tempos de cada membro e rodapé ``<t:R>``.
"""

from __future__ import annotations

import asyncio
import logging
import time

import discord
from discord.ext import commands
from sqlalchemy import select

from src.config import CANAIS
from src.database.conexao import async_session
from src.database.models import PainelPostado
from src.plantao.plantao_ativos_panel import montar_painel_plantao_ativo
from src.plantao.plantao_service import listar_em_servico
from src.utils.relogio import sincronizar_relogio_com_rede

registrador = logging.getLogger(__name__)

NOME_PAINEL = "plantao_ativo"
INTERVALO_SEGUNDOS = 60.0


class PlantaoAtivosTasks(commands.Cog):
    """Cog só de tarefa: painel de plantão ativo a cada 60 s."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._trava = asyncio.Lock()
        self._tarefa: asyncio.Task | None = None
        self._numero_do_ciclo = 0
        self._ultimo_edit_ok = 0.0

    async def cog_load(self) -> None:
        """Sobe o loop depois que o cog está registrado no bot."""
        if self._tarefa is None or self._tarefa.done():
            self._tarefa = asyncio.create_task(
                self._loop_plantao_ativo(),
                name="plantao_ativo_loop",
            )

    def cog_unload(self) -> None:
        if self._tarefa is not None and not self._tarefa.done():
            self._tarefa.cancel()

    async def _loop_plantao_ativo(self) -> None:
        """
        Um ciclo a cada INTERVALO_SEGUNDOS, sem sobreposição.

        O sleep é o que sobra do intervalo depois do trabalho, para o
        ritmo ficar perto de 60 s mesmo se o edit demorar 1–2 s.
        """
        await self.bot.wait_until_ready()
        registrador.info(
            "Loop plantão ativo iniciado (a cada %.0f s, asyncio puro).",
            INTERVALO_SEGUNDOS,
        )

        while not self.bot.is_closed():
            inicio = time.monotonic()
            try:
                await self._rodar_um_ciclo()
            except asyncio.CancelledError:
                raise
            except Exception as erro_capturado:
                registrador.exception(
                    "Plantão ativo: falha no ciclo: %s",
                    erro_capturado,
                )

            decorrido = time.monotonic() - inicio
            espera = INTERVALO_SEGUNDOS - decorrido
            if espera < 1.0:
                espera = 1.0
            try:
                await asyncio.sleep(espera)
            except asyncio.CancelledError:
                raise

    async def _rodar_um_ciclo(self) -> None:
        # Evita reentrada se algo externo chamar o ciclo.
        if self._trava.locked():
            registrador.warning(
                "Plantão ativo: ciclo ignorado (já em andamento)."
            )
            return

        async with self._trava:
            # Corta echo se por algum motivo o ciclo rodou de novo cedo.
            agora = time.monotonic()
            if (
                self._ultimo_edit_ok > 0
                and (agora - self._ultimo_edit_ok) < 50.0
            ):
                registrador.debug(
                    "Plantão ativo: cedo demais (%.0f s); pulando.",
                    agora - self._ultimo_edit_ok,
                )
                return

            self._numero_do_ciclo += 1
            numero = self._numero_do_ciclo

            await sincronizar_relogio_com_rede()
            await self._atualizar_ou_publicar(numero)

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
                self._ultimo_edit_ok = time.monotonic()
                return
            await self._publicar_nova_mensagem(canal, view, registro)
            self._ultimo_edit_ok = time.monotonic()
            return

        await self._publicar_nova_mensagem(canal, view, None)
        self._ultimo_edit_ok = time.monotonic()

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
                    "(message_id=%s). Próximo ciclo.",
                    numero_do_ciclo,
                    message_id,
                )
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
