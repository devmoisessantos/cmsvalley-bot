"""
Comandos de barra do subdomínio chamada.

Grupo /chamada — status do lock, histórico, faltas e correções no banco.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from src.plantao.chamada.chamada_service import (
    admin_buscar_chamada,
    admin_contar_faltas,
    admin_criar_chamada_manual,
    admin_excluir_chamada,
    admin_liberar_lock,
    admin_listar_chamadas,
    admin_listar_faltas,
    admin_remover_falta,
    admin_resetar_cooldown,
    admin_status_controle,
    registrar_falta,
)
from src.utils.mensagens import (
    responder_aviso,
    responder_erro,
    responder_info,
    responder_sucesso,
)
from src.utils.permissions import apenas_administrador


class ChamadaCog(commands.Cog):
    """Administração de chamada de presença."""

    grupo_chamada = app_commands.Group(
        name="chamada",
        description="Administração de chamadas e faltas",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @grupo_chamada.command(
        name="status",
        description="[Admin] Status do lock e cooldown da chamada",
    )
    @apenas_administrador()
    async def chamada_status(self, interacao: discord.Interaction):
        dados = await admin_status_controle()
        linhas = [
            f"**Em andamento:** `{'sim' if dados['chamada_em_andamento'] else 'não'}`",
            f"**Doutor na chamada:** `{dados['doutor_em_chamada_id'] or '—'}`",
            f"**Iniciada em:** `{dados['chamada_iniciada_em'] or '—'}`",
            f"**Última chamada:** `{dados['ultima_chamada_em'] or '—'}`",
            f"**Próximo horário:** `{dados['proximo_horario']}`",
            f"**Liberado agora:** `{'sim' if dados['liberado_agora'] else 'não'}`",
        ]
        await responder_info(
            interacao,
            titulo="Status da chamada",
            linhas=linhas,
            delay=40,
        )

    @grupo_chamada.command(
        name="liberar_lock",
        description="[Admin] Força liberação do lock (chamada travada)",
    )
    @app_commands.describe(
        marcar_cooldown="Se true, também registra cooldown como se tivesse finalizado",
    )
    @apenas_administrador()
    async def chamada_liberar_lock(
        self,
        interacao: discord.Interaction,
        marcar_cooldown: bool = False,
    ):
        estava = await admin_liberar_lock(marcar_cooldown=marcar_cooldown)
        if not estava:
            await responder_aviso(
                interacao,
                titulo="Lock já livre",
                linhas=[
                    "Não havia chamada em andamento.",
                    f"Cooldown atualizado: `{'sim' if marcar_cooldown else 'não'}`.",
                ],
            )
            return
        await responder_sucesso(
            interacao,
            titulo="Lock liberado",
            linhas=[
                "A trava de concorrência foi removida.",
                f"Cooldown registrado: `{'sim' if marcar_cooldown else 'não'}`.",
            ],
        )

    @grupo_chamada.command(
        name="reset_cooldown",
        description="[Admin] Zera ultima_chamada_em (respeita só janela de RR)",
    )
    @apenas_administrador()
    async def chamada_reset_cooldown(self, interacao: discord.Interaction):
        await admin_resetar_cooldown()
        await responder_sucesso(
            interacao,
            titulo="Cooldown zerado",
            linhas=[
                "`ultima_chamada_em` foi limpo.",
                "A próxima chamada ainda precisa respeitar a janela pós-RR, se aplicável.",
            ],
        )

    @grupo_chamada.command(
        name="historico",
        description="[Admin] Últimas chamadas registradas no banco",
    )
    @app_commands.describe(limite="Quantidade (padrão 10, máx 25)")
    @apenas_administrador()
    async def chamada_historico(
        self,
        interacao: discord.Interaction,
        limite: app_commands.Range[int, 1, 25] = 10,
    ):
        lista = await admin_listar_chamadas(limite=limite)
        if not lista:
            await responder_info(
                interacao,
                titulo="Sem histórico",
                linhas=["Nenhuma linha em `chamadas`."],
            )
            return
        linhas = []
        for chamada in lista:
            linhas.append(
                f"`#{chamada.id}` · Responsável: `<@{chamada.doutor_id}>` · "
                f"EMS `{chamada.total_medicos_ems}` · "
                f"toggle `{chamada.total_toggle_ligado}` · "
                f"✓{chamada.total_presentes} ✗{chamada.total_ausentes} · "
                f"`{chamada.criada_em}`\n\n"
            )
        await responder_info(
            interacao,
            titulo=f"Histórico ({len(lista)})",
            linhas=linhas,
            delay=60,
        )

    @grupo_chamada.command(
        name="ver",
        description="[Admin] Detalhe de uma chamada pelo ID",
    )
    @app_commands.describe(chamada_id="ID da tabela chamadas")
    @apenas_administrador()
    async def chamada_ver(
        self,
        interacao: discord.Interaction,
        chamada_id: int,
    ):
        chamada = await admin_buscar_chamada(chamada_id)
        if chamada is None:
            await responder_erro(
                interacao,
                titulo="Não encontrada",
                linhas=[f"Não existe chamada `#{chamada_id}`."],
            )
            return
        await responder_info(
            interacao,
            titulo=f"Chamada #{chamada.id}",
            linhas=[
                f"**Doutor:** `{chamada.doutor_id}`",
                f"**Médicos EMS:** `{chamada.total_medicos_ems}`",
                f"**Toggle ligado:** `{chamada.total_toggle_ligado}`",
                f"**Presentes:** `{chamada.total_presentes}`",
                f"**Ausentes:** `{chamada.total_ausentes}`",
                f"**Criada em:** `{chamada.criada_em}`",
            ],
            delay=40,
        )

    @grupo_chamada.command(
        name="criar",
        description="[Admin] Insere registro manual de chamada no banco",
    )
    @app_commands.describe(
        doutor="Doutor responsável (padrão: você)",
        total_ems="Total no /ems",
        total_toggle="Total com toggle",
        presentes="Presentes",
        ausentes="Ausentes",
    )
    @apenas_administrador()
    async def chamada_criar(
        self,
        interacao: discord.Interaction,
        doutor: discord.Member | None = None,
        total_ems: app_commands.Range[int, 0, 500] = 0,
        total_toggle: app_commands.Range[int, 0, 500] = 0,
        presentes: app_commands.Range[int, 0, 500] = 0,
        ausentes: app_commands.Range[int, 0, 500] = 0,
    ):
        membro_doutor = doutor or interacao.user
        chamada = await admin_criar_chamada_manual(
            doutor_id=membro_doutor.id,
            total_medicos_ems=total_ems,
            total_toggle_ligado=total_toggle,
            total_presentes=presentes,
            total_ausentes=ausentes,
        )
        await responder_sucesso(
            interacao,
            titulo="Chamada criada",
            linhas=[
                f"**ID:** `#{chamada.id}`",
                f"**Doutor:** {membro_doutor.mention}",
            ],
        )

    @grupo_chamada.command(
        name="excluir",
        description="[Admin] Remove um registro de chamada do banco",
    )
    @app_commands.describe(chamada_id="ID da tabela chamadas")
    @apenas_administrador()
    async def chamada_excluir(
        self,
        interacao: discord.Interaction,
        chamada_id: int,
    ):
        ok = await admin_excluir_chamada(chamada_id)
        if not ok:
            await responder_erro(
                interacao,
                titulo="Não encontrada",
                linhas=[f"Chamada `#{chamada_id}` não existe."],
            )
            return
        await responder_sucesso(
            interacao,
            titulo="Chamada excluída",
            linhas=[
                f"Registro `#{chamada_id}` removido.",
                "Faltas com esse `chamada_id` não são apagadas automaticamente.",
            ],
        )

    @grupo_chamada.command(
        name="faltas",
        description="[Admin] Lista faltas (opcional: filtrar por membro)",
    )
    @app_commands.describe(
        membro="Se informado, filtra faltas deste membro",
        limite="Quantidade (padrão 15)",
    )
    @apenas_administrador()
    async def chamada_faltas(
        self,
        interacao: discord.Interaction,
        membro: discord.Member | None = None,
        limite: app_commands.Range[int, 1, 40] = 15,
    ):
        lista = await admin_listar_faltas(
            discord_id=membro.id if membro else None,
            limite=limite,
        )
        if not lista:
            await responder_info(
                interacao,
                titulo="Sem faltas",
                linhas=["Nenhum registro em `faltas_chamada` para esse filtro."],
            )
            return

        linhas = []
        for falta in lista:
            linhas.append(
                f"`Falta #{falta.id}` · Usuário: `<@{falta.discord_id}>` · "
                f"Chamada `#{falta.chamada_id}` · `{falta.motivo}` · "
                f"`{falta.criado_em}`\n"
            )
        extra = ""
        if membro is not None:
            total = await admin_contar_faltas(membro.id)
            extra = f" · total do membro: **{total}**"
        await responder_info(
            interacao,
            titulo=f"Faltas ({len(lista)}){extra}",
            linhas=linhas,
            delay=60,
        )

    @grupo_chamada.command(
        name="remover_falta",
        description="[Admin] Apaga uma falta pelo ID (não remove cargo já dado)",
    )
    @app_commands.describe(falta_id="ID da tabela faltas_chamada")
    @apenas_administrador()
    async def chamada_remover_falta(
        self,
        interacao: discord.Interaction,
        falta_id: int,
    ):
        ok = await admin_remover_falta(falta_id)
        if not ok:
            await responder_erro(
                interacao,
                titulo="Não encontrada",
                linhas=[f"Falta `#{falta_id}` não existe."],
            )
            return
        await responder_sucesso(
            interacao,
            titulo="Falta removida",
            linhas=[
                f"Registro `#{falta_id}` apagado de `faltas_chamada`.",
                "Cargos de punição já aplicados **não** são revertidos por este comando.",
            ],
        )

    @grupo_chamada.command(
        name="registrar_falta",
        description="[Admin] Registra falta manual (dispara lógica de punição)",
    )
    @app_commands.describe(
        membro="Membro que faltou",
        chamada_id="ID da chamada (use 0 se não houver)",
        motivo="Motivo curto",
    )
    @apenas_administrador()
    async def chamada_registrar_falta(
        self,
        interacao: discord.Interaction,
        membro: discord.Member,
        chamada_id: int = 0,
        motivo: str = "Registro manual (admin)",
    ):
        if interacao.guild is None:
            await responder_erro(
                interacao,
                titulo="Só no servidor",
                linhas=["Este comando precisa ser usado dentro da guilda."],
            )
            return
        total = await registrar_falta(
            membro.id,
            chamada_id,
            motivo[:100],
            interacao.guild,
        )
        await responder_sucesso(
            interacao,
            titulo="Falta registrada",
            linhas=[
                f"**Membro:** {membro.mention}",
                f"**Chamada:** `#{chamada_id}`",
                f"**Total de faltas agora:** `{total}`",
                "A lógica de punição/aviso foi executada se aplicável.",
            ],
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(ChamadaCog(bot))
