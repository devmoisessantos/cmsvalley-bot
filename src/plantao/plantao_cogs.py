"""
Comandos de barra do domínio plantão.

Grupo /plantao — consulta e administração de estado_plantao.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from src.plantao.plantao_panel import InformacoesPlantaoView, _buscar_estado
from src.plantao.plantao_service import (
    admin_definir_moedas,
    admin_forcar_desligar,
    admin_limpar_estado,
    consultar_estado_plantao,
    listar_em_servico,
)
from src.utils.mensagens import (
    responder_erro,
    responder_info,
    responder_sucesso,
    responder_view,
)
from src.utils.permissions import apenas_administrador


class PlantaoCog(commands.Cog):
    """Comandos de plantão (membro + admin)."""

    grupo_plantao = app_commands.Group(
        name="plantao",
        description="Consulta e administração do plantão",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ------------------------------------------------------------------
    # Membro
    # ------------------------------------------------------------------

    @grupo_plantao.command(
        name="info",
        description="Veja seu status atual de plantão",
    )
    async def plantao_info(self, interacao: discord.Interaction):
        estado = await _buscar_estado(interacao.user.id)
        view = InformacoesPlantaoView(interacao.user, estado)
        await responder_view(interacao, view, ephemeral=True)

    # ------------------------------------------------------------------
    # Admin — consulta
    # ------------------------------------------------------------------

    @grupo_plantao.command(
        name="consultar",
        description="[Admin] Consulta o estado de plantão de um membro",
    )
    @app_commands.describe(membro="Membro a consultar")
    @apenas_administrador()
    async def plantao_consultar(
        self,
        interacao: discord.Interaction,
        membro: discord.Member,
    ):
        estado = await consultar_estado_plantao(membro.id)
        if estado is None:
            await responder_info(
                interacao,
                titulo="Sem registro",
                linhas=[
                    f"{membro.mention} ainda não tem linha em `estado_plantao`.",
                ],
            )
            return

        linhas = [
            f"**Membro:** {membro.mention} (`{membro.id}`)",
            f"**ID FiveM:** `{estado.id_fivem or '—'}`",
            f"**Toggle:** `{'ligado' if estado.toggle_ligado else 'desligado'}`",
            f"**Em call válida:** `{'sim' if estado.em_call_valida else 'não'}`",
            f"**Canal atual:** `{estado.canal_atual_id or '—'}`",
            f"**Moedas:** `{estado.saldo_moedas}`",
            f"**Segundos acumulados:** `{estado.segundos_acumulados}`",
            f"**Modo coordenação:** `{'sim' if estado.modo_coordenacao else 'não'}`",
            f"**Lembretes ociosidade:** "
            f"`{estado.lembrete_1_enviado}/{estado.lembrete_2_enviado}/{estado.lembrete_3_enviado}`",
            f"**Última atualização:** `{estado.ultima_atualizacao}`",
        ]
        await responder_info(
            interacao,
            titulo="Estado de plantão",
            linhas=linhas,
            delay=45,
        )

    @grupo_plantao.command(
        name="ativos",
        description="[Admin] Lista membros com toggle ligado",
    )
    @apenas_administrador()
    async def plantao_ativos(self, interacao: discord.Interaction):
        lista = await listar_em_servico(limite=40)
        if not lista:
            await responder_info(
                interacao,
                titulo="Ninguém em serviço",
                linhas=["Nenhum membro com toggle ligado no banco."],
            )
            return

        linhas: list[str] = []
        for estado in lista:
            linhas.append(
                f"`{estado.discord_id}` · ID `{estado.id_fivem or '—'}` · "
                f"moedas `{estado.saldo_moedas}` · "
                f"call `{'sim' if estado.em_call_valida else 'não'}`"
            )
        await responder_info(
            interacao,
            titulo=f"Em serviço ({len(lista)})",
            linhas=linhas,
            delay=60,
        )

    # ------------------------------------------------------------------
    # Admin — mutação
    # ------------------------------------------------------------------

    @grupo_plantao.command(
        name="set_moedas",
        description="[Admin] Define o saldo de moedas de um membro",
    )
    @app_commands.describe(
        membro="Membro alvo",
        saldo="Novo saldo (inteiro ≥ 0)",
    )
    @apenas_administrador()
    async def plantao_set_moedas(
        self,
        interacao: discord.Interaction,
        membro: discord.Member,
        saldo: app_commands.Range[int, 0, 1_000_000],
    ):
        estado = await admin_definir_moedas(membro.id, saldo)
        await responder_sucesso(
            interacao,
            titulo="Moedas atualizadas",
            linhas=[
                f"**Membro:** {membro.mention}",
                f"**Novo saldo:** `{estado.saldo_moedas}`",
            ],
        )

    @grupo_plantao.command(
        name="forcar_desligar",
        description="[Admin] Desliga o plantão de um membro no banco",
    )
    @app_commands.describe(membro="Membro a desligar")
    @apenas_administrador()
    async def plantao_forcar_desligar(
        self,
        interacao: discord.Interaction,
        membro: discord.Member,
    ):
        desligou = await admin_forcar_desligar(membro.id)
        if not desligou:
            await responder_erro(
                interacao,
                titulo="Nada a fazer",
                linhas=[
                    f"{membro.mention} já estava desligado ou sem registro.",
                ],
            )
            return
        await responder_sucesso(
            interacao,
            titulo="Plantão desligado",
            linhas=[
                f"{membro.mention} teve o toggle forçado para **desligado** no banco.",
                "Campos de call/ociosidade foram zerados.",
            ],
        )

    @grupo_plantao.command(
        name="limpar_estado",
        description="[Admin] Apaga o registro estado_plantao do membro",
    )
    @app_commands.describe(membro="Membro cujo registro será apagado")
    @apenas_administrador()
    async def plantao_limpar_estado(
        self,
        interacao: discord.Interaction,
        membro: discord.Member,
    ):
        existia = await admin_limpar_estado(membro.id)
        if not existia:
            await responder_erro(
                interacao,
                titulo="Sem registro",
                linhas=[f"{membro.mention} não tinha linha em `estado_plantao`."],
            )
            return
        await responder_sucesso(
            interacao,
            titulo="Estado apagado",
            linhas=[
                f"Registro de {membro.mention} removido de `estado_plantao`.",
            ],
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(PlantaoCog(bot))
