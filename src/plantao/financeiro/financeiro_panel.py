# src/plantao/financeiro/financeiro_panel.py
"""
Painel ephemeral completo de gestão financeira do plantão (Components V2).

Comando: /plantao painel-financeiro

Hub → busca membro → carteira, análise, ajuste, recálculo, YAML.
Atalhos globais: regras, alertas de acumulado, fila de dump, cotações.
"""

from __future__ import annotations

import io
import logging

import discord

from src.config import VALOR_MOEDA_POR_CARGO
from src.plantao.financeiro.carteira.carteira_service import (
    membro_na_hierarquia,
)
from src.plantao.financeiro.financeiro_admin_service import (
    ajustar_saldo_com_motivo,
    aplicar_recalculo_por_logs,
    corrigir_todos_acumulados_excesso,
    simular_recalculo_por_logs,
    zerar_segundos_acumulados_excesso,
)
from src.plantao.financeiro.financeiro_service import (
    AnaliseCompletaMembro,
    ResumoCarteiraMembro,
    analisar_membro_completo,
    buscar_discord_id_por_fivem,
    buscar_estado_e_carteira,
    filtrar_irregulares_da_hierarquia,
    gerar_yaml_completo_membro,
    listar_alertas_acumulado_travado,
    listar_membros_irregulares,
    listar_suspeitos_dump_moedas,
)
from src.plantao.financeiro.moeda_helper import (
    SEVERIDADE_CRITICO,
    SEVERIDADE_GRAVE,
    SEVERIDADE_LEVE,
)
from src.plantao.financeiro.regras_moeda_service import (
    carregar_regras_do_banco,
    hora_limite_moeda_sabado,
    resumo_regras,
    salvar_regras,
    segundos_para_moeda,
    teto_moedas_por_dia,
    teto_moedas_por_semana,
)
from src.utils.error_handling import LoggingViewMixin
from src.utils.formatacao import (
    formatar_data_hora_local,
    formatar_dinheiro,
    formatar_hms,
)
from src.utils.mensagens import (
    responder_erro,
    responder_info,
    responder_sucesso,
    responder_view,
)

registrador = logging.getLogger(__name__)


def _texto_regras() -> str:
    return (
        f"> `1` moeda a cada `{segundos_para_moeda() // 60}` min "
        f"(`{segundos_para_moeda()}s`)\n"
        f"> Teto: `{teto_moedas_por_dia()}` / dia · "
        f"`{teto_moedas_por_semana()}` / semana\n"
        f"> Domingo: sem moeda · Sábado: só até "
        f"`{hora_limite_moeda_sabado()}h`\n"
        f"> AFK (surdo): tempo pausado\n"
        f"> Blocos acima do teto **não** viram moeda no dia seguinte"
    )


async def _checar_dono_painel(
    interacao: discord.Interaction,
    dono_id: int,
) -> bool:
    if interacao.user.id == dono_id:
        return True
    await responder_erro(
        interacao,
        titulo="Painel de outro administrador",
        linhas=["Este painel é só de quem abriu o comando."],
    )
    return False


class BotaoBuscarMembro(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Buscar membro",
            style=discord.ButtonStyle.primary,
            custom_id="fin:buscar",
        )

    async def callback(self, interacao: discord.Interaction) -> None:
        await interacao.response.send_modal(ModalBuscarMembroFinanceiro())


class BotaoVerRegras(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Ver regras",
            style=discord.ButtonStyle.secondary,
            custom_id="fin:regras",
        )

    async def callback(self, interacao: discord.Interaction) -> None:
        regras = resumo_regras()
        await responder_info(
            interacao,
            titulo="Regras de recompensa",
            linhas=[
                f"1 moeda / {segundos_para_moeda()} s de call válida",
                f"Teto diário: {teto_moedas_por_dia()}",
                f"Teto semanal: {teto_moedas_por_semana()}",
                f"Sábado sem moeda a partir de {hora_limite_moeda_sabado()}h",
                f"Última edição: {regras.get('atualizado_em') or 'padrão do config'}",
            ],
            delay=60,
        )


class BotaoConfigurarRegras(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Configurar regras",
            style=discord.ButtonStyle.primary,
            custom_id="fin:cfg_regras",
        )

    async def callback(self, interacao: discord.Interaction) -> None:
        await interacao.response.send_modal(ModalConfigurarRegras())


class BotaoCotacoes(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Cotações",
            style=discord.ButtonStyle.secondary,
            custom_id="fin:cotacoes",
        )

    async def callback(self, interacao: discord.Interaction) -> None:
        linhas = [
            f"**{nome}:** {formatar_dinheiro(valor)} / moeda"
            for nome, valor in VALOR_MOEDA_POR_CARGO.items()
        ]
        await responder_info(
            interacao,
            titulo="Cotação da moeda por cargo",
            linhas=linhas or ["Nenhuma cotação configurada."],
            delay=60,
        )


class BotaoAlertasAcumulado(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Alertas acumulado",
            style=discord.ButtonStyle.danger,
            custom_id="fin:alertas",
        )

    async def callback(self, interacao: discord.Interaction) -> None:
        await interacao.response.defer(ephemeral=True)
        alertas = await listar_alertas_acumulado_travado(limite=25)
        if not alertas:
            await interacao.followup.send(
                content=(
                    "Nenhum membro com `segundos_acumulados` ≥ "
                    f"{segundos_para_moeda()}s. Banco limpo."
                ),
                ephemeral=True,
            )
            return
        linhas = []
        for item in alertas[:20]:
            linhas.append(
                f"<@{item.discord_id}> · `{item.segundos_acumulados}s` "
                f"({item.blocos_completos} blocos) · saldo `{item.saldo_moedas}` "
                f"· toggle `{'on' if item.toggle_ligado else 'off'}`"
            )
        view = PainelListaAlertas(
            administrador=interacao.user,  # type: ignore[arg-type]
            alertas_ids=[a.discord_id for a in alertas],
            texto="\n".join(f"• {linha}" for linha in linhas),
        )
        await interacao.followup.send(view=view, ephemeral=True)


class BotaoMembrosIrregulares(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Membros irregulares",
            style=discord.ButtonStyle.danger,
            custom_id="fin:irregulares",
        )

    async def callback(self, interacao: discord.Interaction) -> None:
        await interacao.response.defer(ephemeral=True)
        guilda = interacao.guild
        irregulares = await listar_membros_irregulares(limite=80)

        so_hierarquia = False
        if guilda is not None:
            ids_hierarquia = {
                membro.id
                for membro in guilda.members
                if isinstance(membro, discord.Member)
                and membro_na_hierarquia(membro)
            }
            filtrados = filtrar_irregulares_da_hierarquia(
                irregulares, ids_hierarquia
            )
            if filtrados:
                irregulares = filtrados
                so_hierarquia = True

        if not irregulares:
            await interacao.followup.send(
                content=(
                    "Nenhum membro irregular na hierarquia. "
                    "Acumulado limpo e sem crédito em lote no extrato."
                ),
                ephemeral=True,
            )
            return

        por_nivel = {
            SEVERIDADE_CRITICO: [],
            SEVERIDADE_GRAVE: [],
            SEVERIDADE_LEVE: [],
        }
        for item in irregulares:
            por_nivel.setdefault(item.severidade, []).append(item)

        blocos: list[str] = []
        for nivel in (SEVERIDADE_CRITICO, SEVERIDADE_GRAVE, SEVERIDADE_LEVE):
            lista = por_nivel.get(nivel) or []
            if not lista:
                continue
            titulo_nivel = {
                SEVERIDADE_CRITICO: "CRITICO",
                SEVERIDADE_GRAVE: "GRAVE",
                SEVERIDADE_LEVE: "LEVE",
            }[nivel]
            linhas_nivel = [f"**{titulo_nivel}** ({len(lista)})"]
            for item in lista[:12]:
                fivem = f"`{item.id_fivem}`" if item.id_fivem else "—"
                linhas_nivel.append(
                    f"• <@{item.discord_id}> · FID {fivem} · "
                    f"acum `{item.segundos_acumulados}s` · "
                    f"lotes `{item.qtd_creditos_lote}` "
                    f"({item.moedas_em_lote} moedas) · "
                    f"saldo `{item.saldo_moedas}`"
                )
            blocos.append("\n".join(linhas_nivel))

        texto = "\n\n".join(blocos)
        ids_com_acumulado = [
            item.discord_id
            for item in irregulares
            if int(item.segundos_acumulados or 0) >= segundos_para_moeda()
        ]
        escopo = (
            "hierarquia online no servidor"
            if so_hierarquia
            else "todos com estado irregular"
        )
        view = PainelIrregularesLote(
            administrador=interacao.user,  # type: ignore[arg-type]
            ids_para_limpar=ids_com_acumulado,
            texto=texto,
            escopo=escopo,
        )
        await interacao.followup.send(view=view, ephemeral=True)


class BotaoFilaDump(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Fila dump 14",
            style=discord.ButtonStyle.danger,
            custom_id="fin:dump",
        )

    async def callback(self, interacao: discord.Interaction) -> None:
        await interacao.response.defer(ephemeral=True)
        suspeitos = await listar_suspeitos_dump_moedas(limite=30)
        if not suspeitos:
            await interacao.followup.send(
                content=(
                    "Nenhum dia com crédito de plantão ≥ teto diário "
                    "encontrado no extrato."
                ),
                ephemeral=True,
            )
            return
        linhas = []
        for item in suspeitos[:20]:
            linhas.append(
                f"<@{item.discord_id}> · `{item.data_local}` · "
                f"**{item.moedas_no_dia}** moedas · "
                f"{item.primeira_credito_em} → {item.ultima_credito_em}"
            )
        texto = "\n".join(f"• {linha}" for linha in linhas)
        view = discord.ui.LayoutView(timeout=300)
        view.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(
                    "# 🚨 Fila de revisão — possível dump de moedas\n"
                    "Dias em que o extrato de plantão atingiu o teto diário "
                    "(heurística do bug antigo). Revise caso a caso.\n\n"
                    f"{texto}"
                ),
                discord.ui.TextDisplay(
                    "-# Não altera saldo · use Buscar membro para corrigir"
                ),
            )
        )
        await interacao.followup.send(view=view, ephemeral=True)


class PainelFinanceiroHub(LoggingViewMixin, discord.ui.LayoutView):
    """Hub principal do painel financeiro."""

    def __init__(self, administrador: discord.Member):
        super().__init__(timeout=420)
        self.administrador_id = administrador.id

        linha1 = discord.ui.ActionRow()
        linha1.add_item(BotaoBuscarMembro())
        linha1.add_item(BotaoVerRegras())
        linha1.add_item(BotaoConfigurarRegras())
        linha1.add_item(BotaoCotacoes())

        linha2 = discord.ui.ActionRow()
        linha2.add_item(BotaoMembrosIrregulares())
        linha2.add_item(BotaoAlertasAcumulado())
        linha2.add_item(BotaoFilaDump())

        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(
                    "# Painel financeiro — Plantão\n"
                    "Prova de falhas: regras, auditoria, correção e extrato.\n\n"
                    "**Regra vigente**\n"
                    f"{_texto_regras()}\n\n"
                    "**Ações**\n"
                    "• Buscar membro — carteira, análise, ajuste, YAML\n"
                    "• Configurar regras — segundos/moeda e tetos\n"
                    "• Membros irregulares — LEVE / GRAVE / CRITICO\n"
                    "• Alertas acumulado — blocos travados no estado\n"
                    "• Fila dump 14 — créditos em lote no extrato"
                ),
                linha1,
                linha2,
                discord.ui.TextDisplay(
                    "-# Somente administradores · respostas efêmeras"
                ),
            )
        )

    async def interaction_check(
        self, interacao: discord.Interaction
    ) -> bool:
        return await _checar_dono_painel(interacao, self.administrador_id)


class PainelIrregularesLote(LoggingViewMixin, discord.ui.LayoutView):
    """Lista irregulares por severidade + correção em massa do acumulado."""

    def __init__(
        self,
        administrador: discord.Member,
        ids_para_limpar: list[int],
        texto: str,
        escopo: str,
    ):
        super().__init__(timeout=420)
        self.administrador_id = administrador.id
        self.ids_para_limpar = list(ids_para_limpar)

        linha = discord.ui.ActionRow()
        botao_limpar = discord.ui.Button(
            label="Corrigir acumulados listados",
            style=discord.ButtonStyle.danger,
            custom_id="fin:corrigir_acumulados_lote",
        )

        async def ao_corrigir(interacao: discord.Interaction) -> None:
            if not await _checar_dono_painel(interacao, self.administrador_id):
                return
            if not isinstance(interacao.user, discord.Member):
                await responder_erro(
                    interacao,
                    titulo="Fora do servidor",
                    linhas=["Só funciona dentro do servidor."],
                )
                return
            await interacao.response.defer(ephemeral=True)
            if not self.ids_para_limpar:
                await interacao.followup.send(
                    content=(
                        "Nenhum da lista tem bloco completo parado. "
                        "Créditos em lote no extrato exigem ajuste manual "
                        "membro a membro (decisão da diretoria)."
                    ),
                    ephemeral=True,
                )
                return
            ok_n, falha_n, relatorio = await corrigir_todos_acumulados_excesso(
                interacao.user,
                apenas_discord_ids=self.ids_para_limpar,
                limite=80,
            )
            cabecalho = (
                f"**Correção em lote** — OK `{ok_n}` · falha `{falha_n}`\n"
                "Só removeu blocos completos de `segundos_acumulados`. "
                "Não creditou moeda e não reescreveu extrato antigo.\n\n"
            )
            corpo = "\n".join(relatorio[:30])
            await interacao.followup.send(
                content=(cabecalho + corpo)[:1900],
                ephemeral=True,
            )

        botao_limpar.callback = ao_corrigir  # type: ignore[method-assign]
        linha.add_item(botao_limpar)

        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(
                    "# Prova de falhas — membros irregulares\n"
                    f"Escopo: **{escopo}**\n"
                    "Severidade pela regra vigente "
                    "(1 moeda/bloco · teto · acumulado só com resto).\n\n"
                    f"{texto}\n\n"
                    "**Corrigir acumulados listados** descarta blocos "
                    "completos parados (não paga moeda). "
                    "Histórico de lote no extrato → Fila dump / ajuste "
                    "manual com motivo."
                ),
                linha,
                discord.ui.TextDisplay(
                    "-# Ordenado do mais crítico · Buscar membro para YAML"
                ),
            )
        )

    async def interaction_check(
        self, interacao: discord.Interaction
    ) -> bool:
        return await _checar_dono_painel(interacao, self.administrador_id)


class PainelListaAlertas(LoggingViewMixin, discord.ui.LayoutView):
    def __init__(
        self,
        administrador: discord.Member,
        alertas_ids: list[int],
        texto: str,
    ):
        super().__init__(timeout=300)
        self.administrador_id = administrador.id
        self.alertas_ids = list(alertas_ids)

        linha = discord.ui.ActionRow()
        botao_limpar = discord.ui.Button(
            label="Limpar excessos listados",
            style=discord.ButtonStyle.danger,
            custom_id="fin:limpar_todos_alertas",
        )

        async def ao_limpar(interacao: discord.Interaction) -> None:
            if not await _checar_dono_painel(interacao, self.administrador_id):
                return
            if not isinstance(interacao.user, discord.Member):
                await responder_erro(
                    interacao,
                    titulo="Fora do servidor",
                    linhas=["Só funciona dentro do servidor."],
                )
                return
            await interacao.response.defer(ephemeral=True)
            relatorio: list[str] = []
            for discord_id in self.alertas_ids[:25]:
                ok, msg = await zerar_segundos_acumulados_excesso(
                    discord_id, interacao.user
                )
                relatorio.append(("✅ " if ok else "❌ ") + msg)
            await interacao.followup.send(
                content="\n".join(relatorio)[:1900],
                ephemeral=True,
            )

        botao_limpar.callback = ao_limpar  # type: ignore[method-assign]
        linha.add_item(botao_limpar)

        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(
                    "# ⚠️ Segundos acumulados travados\n"
                    f"Limite de 1 bloco: `{segundos_para_moeda()}s`\n\n"
                    f"{texto}"
                ),
                linha,
                discord.ui.TextDisplay(
                    "-# Limpar descarta só blocos completos; resto incompleto fica"
                ),
            )
        )

    async def interaction_check(
        self, interacao: discord.Interaction
    ) -> bool:
        return await _checar_dono_painel(interacao, self.administrador_id)


class ModalBuscarMembroFinanceiro(discord.ui.Modal, title="Buscar membro"):
    campo_busca = discord.ui.TextInput(
        label="Menção, ID Discord ou ID FiveM",
        placeholder="@membro · 123456789012345678 · 42",
        required=True,
        max_length=80,
    )

    async def on_submit(self, interacao: discord.Interaction) -> None:
        bruto = (self.campo_busca.value or "").strip()
        discord_id = await _resolver_discord_id(interacao, bruto)
        if discord_id is None:
            await responder_erro(
                interacao,
                titulo="Membro não encontrado",
                linhas=[
                    "Não encontrei membro com esse valor.",
                    "Use menção, ID Discord numérico ou ID FiveM cadastrado.",
                ],
            )
            return

        await interacao.response.defer(ephemeral=True)
        try:
            view = await montar_painel_membro(interacao, discord_id)
            await interacao.followup.send(view=view, ephemeral=True)
        except Exception as erro_ao_abrir:
            registrador.exception(
                "Falha ao abrir painel financeiro do membro %s: %s",
                discord_id,
                erro_ao_abrir,
            )
            await interacao.followup.send(
                content="Não consegui montar o painel deste membro agora.",
                ephemeral=True,
            )


class ModalConfigurarRegras(discord.ui.Modal, title="Configurar regras de moeda"):
    campo_segundos = discord.ui.TextInput(
        label="Segundos por moeda (ex: 1800)",
        default=str(segundos_para_moeda()),
        required=True,
        max_length=6,
    )
    campo_teto_dia = discord.ui.TextInput(
        label="Teto de moedas por dia",
        default=str(teto_moedas_por_dia()),
        required=True,
        max_length=4,
    )
    campo_teto_semana = discord.ui.TextInput(
        label="Teto de moedas por semana",
        default=str(teto_moedas_por_semana()),
        required=True,
        max_length=4,
    )
    campo_hora_sabado = discord.ui.TextInput(
        label="Hora limite sábado (0-23)",
        default=str(hora_limite_moeda_sabado()),
        required=True,
        max_length=2,
    )

    async def on_submit(self, interacao: discord.Interaction) -> None:
        try:
            segundos = int((self.campo_segundos.value or "").strip())
            teto_dia = int((self.campo_teto_dia.value or "").strip())
            teto_semana = int((self.campo_teto_semana.value or "").strip())
            hora_sabado = int((self.campo_hora_sabado.value or "").strip())
        except ValueError:
            await responder_erro(
                interacao,
                titulo="Valores inválidos",
                linhas=["Informe apenas números inteiros."],
            )
            return

        if segundos < 60:
            await responder_erro(
                interacao,
                titulo="Segundos inválidos",
                linhas=["Mínimo: 60 segundos por moeda."],
            )
            return

        regras = await salvar_regras(
            segundos=segundos,
            teto_dia=teto_dia,
            teto_semana=teto_semana,
            hora_sabado=hora_sabado,
            atualizado_por=interacao.user.id,
        )

        if interacao.guild is not None and isinstance(
            interacao.user, discord.Member
        ):
            try:
                from src.plantao.auditoria_service import registrar_auditoria_admin

                await registrar_auditoria_admin(
                    interacao.guild,
                    executor=interacao.user,
                    alvo=None,
                    acao="CONFIGURAR_REGRAS_MOEDA",
                    detalhes=(
                        f"{regras['segundos_para_moeda']}s/moeda · "
                        f"teto dia {regras['teto_moedas_por_dia']} · "
                        f"semana {regras['teto_moedas_por_semana']} · "
                        f"sábado <{regras['hora_limite_moeda_sabado']}h"
                    ),
                )
            except Exception:
                registrador.exception("Falha ao auditar mudança de regras")

        await responder_sucesso(
            interacao,
            titulo="Regras atualizadas",
            linhas=[
                f"Segundos por moeda: `{regras['segundos_para_moeda']}`",
                f"Teto dia: `{regras['teto_moedas_por_dia']}`",
                f"Teto semana: `{regras['teto_moedas_por_semana']}`",
                f"Sábado até: `{regras['hora_limite_moeda_sabado']}h`",
            ],
        )


class ModalAjustarSaldo(discord.ui.Modal, title="Ajustar saldo de moedas"):
    def __init__(self, discord_id: int, saldo_atual: int):
        super().__init__()
        self.discord_id_alvo = discord_id
        self.campo_saldo = discord.ui.TextInput(
            label="Novo saldo absoluto",
            default=str(saldo_atual),
            required=True,
            max_length=8,
        )
        self.campo_motivo = discord.ui.TextInput(
            label="Motivo (obrigatório, min. 5 chars)",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=180,
            placeholder="Ex: correção do dump de 14 moedas em 05/11",
        )
        self.add_item(self.campo_saldo)
        self.add_item(self.campo_motivo)

    async def on_submit(self, interacao: discord.Interaction) -> None:
        if not isinstance(interacao.user, discord.Member):
            await responder_erro(
                interacao,
                titulo="Fora do servidor",
                linhas=["Só funciona dentro do servidor."],
            )
            return
        try:
            novo_saldo = int((self.campo_saldo.value or "").strip())
        except ValueError:
            await responder_erro(
                interacao,
                titulo="Saldo inválido",
                linhas=["Informe um número inteiro."],
            )
            return

        resultado = await ajustar_saldo_com_motivo(
            discord_id=self.discord_id_alvo,
            novo_saldo=novo_saldo,
            motivo=self.campo_motivo.value or "",
            executor=interacao.user,
        )
        if not resultado.ok:
            await responder_erro(
                interacao,
                titulo="Ajuste recusado",
                linhas=[resultado.mensagem],
            )
            return
        await responder_sucesso(
            interacao,
            titulo="Saldo ajustado",
            linhas=[resultado.mensagem],
        )


class ModalConfirmarRecalculo(discord.ui.Modal, title="Confirmar recálculo"):
    def __init__(self, discord_id: int, saldo_sugerido: int, delta: int):
        super().__init__()
        self.discord_id_alvo = discord_id
        self.campo_motivo = discord.ui.TextInput(
            label="Motivo da correção (obrigatório)",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=160,
            placeholder=(
                f"Aplicar saldo {saldo_sugerido} (delta {delta:+d}) "
                "após revisão dos logs"
            ),
        )
        self.add_item(self.campo_motivo)

    async def on_submit(self, interacao: discord.Interaction) -> None:
        if not isinstance(interacao.user, discord.Member):
            await responder_erro(
                interacao,
                titulo="Fora do servidor",
                linhas=["Só funciona dentro do servidor."],
            )
            return
        resultado = await aplicar_recalculo_por_logs(
            discord_id=self.discord_id_alvo,
            executor=interacao.user,
            motivo=self.campo_motivo.value or "",
        )
        if not resultado.ok:
            await responder_erro(
                interacao,
                titulo="Recálculo não aplicado",
                linhas=[resultado.mensagem],
            )
            return
        await responder_sucesso(
            interacao,
            titulo="Recálculo aplicado",
            linhas=[resultado.mensagem],
        )


async def _resolver_discord_id(
    interacao: discord.Interaction,
    bruto: str,
) -> int | None:
    texto = bruto.strip()
    if texto.startswith("<@") and texto.endswith(">"):
        numeros = "".join(ch for ch in texto if ch.isdigit())
        if numeros:
            return int(numeros)

    if texto.isdigit():
        candidato = int(texto)
        if len(texto) >= 16:
            return candidato
        por_fivem = await buscar_discord_id_por_fivem(texto)
        if por_fivem is not None:
            return por_fivem
        if interacao.guild is not None:
            membro = interacao.guild.get_member(candidato)
            if membro is not None:
                return candidato
        return candidato

    return await buscar_discord_id_por_fivem(texto)


def _linhas_carteira(resumo: ResumoCarteiraMembro) -> str:
    canal = (
        f"<#{resumo.canal_atual_id}>"
        if resumo.canal_atual_id
        else "_fora de call_"
    )
    afk = (
        f"`desde {formatar_data_hora_local(resumo.afk_desde)}`"
        if resumo.afk_desde
        else "`não`"
    )
    lotes_txt = (
        ", ".join(
            f"{qtd}@{formatar_dinheiro(cot)}" for cot, qtd in resumo.lotes
        )
        if resumo.lotes
        else "nenhum lote"
    )
    return (
        f"> **Saldo:** `{resumo.saldo_moedas}` moedas "
        f"({formatar_dinheiro(resumo.valor_total_lotes)})\n"
        f"> **Lotes:** {lotes_txt}\n"
        f"> **Toggle:** `{'ligado' if resumo.toggle_ligado else 'desligado'}`\n"
        f"> **Call válida:** `{'sim' if resumo.em_call_valida else 'não'}` · {canal}\n"
        f"> **AFK:** {afk}\n"
        f"> **Acumulado:** `{resumo.segundos_acumulados}s` + "
        f"segmento aberto `{resumo.segmento_aberto_segundos}s`\n"
        f"> **Progresso próxima moeda:** "
        f"`{resumo.progresso_proxima_moeda_segundos}s` / "
        f"`{segundos_para_moeda()}s` "
        f"(faltam `{formatar_hms(resumo.segundos_para_proxima_moeda)}`)\n"
        f"> **Teto hoje:** `{resumo.moedas_ganhas_no_dia}` / "
        f"`{teto_moedas_por_dia()}` · semana "
        f"`{resumo.moedas_ganhas_na_semana}` / `{teto_moedas_por_semana()}`\n"
        f"> **FiveM:** `{resumo.id_fivem or '—'}`"
    )


def _linhas_analise(analise: AnaliseCompletaMembro) -> str:
    avisos = (
        "\n".join(f"> ⚠️ {a}" for a in analise.avisos)
        if analise.avisos
        else "> Nenhum aviso automático."
    )
    return (
        f"> **Eventos de log:** `{analise.total_eventos_log}`\n"
        f"> **Primeiro / último:** "
        f"`{formatar_data_hora_local(analise.primeiro_evento_em)}` → "
        f"`{formatar_data_hora_local(analise.ultimo_evento_em)}`\n"
        f"> **Tempo em call (logs):** "
        f"`{formatar_hms(analise.segundos_em_call_registrados)}`\n"
        f"> **Tempo AFK registrado:** "
        f"`{formatar_hms(analise.segundos_afk_registrados)}`\n"
        f"> **Úteis estimados:** "
        f"`{formatar_hms(analise.segundos_uteis_estimados)}`\n"
        f"> **Moedas esperadas (bruto):** "
        f"`{analise.moedas_esperadas_brutas}`\n"
        f"> **Moedas de plantão no extrato:** "
        f"`{analise.moedas_ganhas_plantao_extrato}`\n"
        f"> **Outras movimentações (líquido):** "
        f"`{analise.moedas_outras_movimentacoes}`\n"
        f"> **Saldo atual:** `{analise.saldo_atual}`\n"
        f"> **Diferença (esperado − extrato):** "
        f"`{analise.diferenca_esperada_vs_extrato}`\n\n"
        f"**Avisos**\n{avisos}"
    )


class BotaoBaixarYaml(discord.ui.Button):
    def __init__(self, discord_id: int) -> None:
        super().__init__(
            label="Baixar YAML",
            style=discord.ButtonStyle.success,
            custom_id=f"fin:yaml:{discord_id}",
        )
        self.discord_id_alvo = discord_id

    async def callback(self, interacao: discord.Interaction) -> None:
        await interacao.response.defer(ephemeral=True, thinking=True)
        try:
            yaml_texto = await gerar_yaml_completo_membro(self.discord_id_alvo)
            arquivo = discord.File(
                io.BytesIO(yaml_texto.encode("utf-8")),
                filename=f"auditoria_plantao_{self.discord_id_alvo}.yaml",
            )
            await interacao.followup.send(
                content=(
                    f"Auditoria completa de <@{self.discord_id_alvo}> "
                    f"(logs + extrato + estado)."
                ),
                file=arquivo,
                ephemeral=True,
            )
        except Exception as erro_yaml:
            registrador.exception(
                "Falha ao gerar YAML do membro %s: %s",
                self.discord_id_alvo,
                erro_yaml,
            )
            await interacao.followup.send(
                content="Não consegui gerar o arquivo YAML agora.",
                ephemeral=True,
            )


class BotaoAjustarSaldo(discord.ui.Button):
    def __init__(self, discord_id: int, saldo_atual: int) -> None:
        super().__init__(
            label="Ajustar saldo",
            style=discord.ButtonStyle.primary,
            custom_id=f"fin:ajuste:{discord_id}",
        )
        self.discord_id_alvo = discord_id
        self.saldo_atual = saldo_atual

    async def callback(self, interacao: discord.Interaction) -> None:
        await interacao.response.send_modal(
            ModalAjustarSaldo(self.discord_id_alvo, self.saldo_atual)
        )


class BotaoRecalcular(discord.ui.Button):
    def __init__(self, discord_id: int) -> None:
        super().__init__(
            label="Recalcular logs",
            style=discord.ButtonStyle.primary,
            custom_id=f"fin:recalc:{discord_id}",
        )
        self.discord_id_alvo = discord_id

    async def callback(self, interacao: discord.Interaction) -> None:
        await interacao.response.defer(ephemeral=True)
        simulacao = await simular_recalculo_por_logs(self.discord_id_alvo)
        avisos = "\n".join(f"• {a}" for a in simulacao.avisos[:6])
        view = PainelConfirmacaoRecalculo(
            administrador=interacao.user,  # type: ignore[arg-type]
            discord_id=self.discord_id_alvo,
            simulacao_texto=(
                f"**Esperado bruto:** `{simulacao.moedas_esperadas_brutas}`\n"
                f"**Extrato plantão:** `{simulacao.moedas_no_extrato_plantao}`\n"
                f"**Saldo atual:** `{simulacao.saldo_atual}`\n"
                f"**Saldo sugerido:** `{simulacao.saldo_sugerido}` "
                f"(delta `{simulacao.delta_sugerido:+d}`)\n\n"
                f"{avisos}"
            ),
            saldo_sugerido=simulacao.saldo_sugerido,
            delta=simulacao.delta_sugerido,
        )
        await interacao.followup.send(view=view, ephemeral=True)


class BotaoLimparAcumulado(discord.ui.Button):
    def __init__(self, discord_id: int) -> None:
        super().__init__(
            label="Limpar acumulado",
            style=discord.ButtonStyle.secondary,
            custom_id=f"fin:limpar_acum:{discord_id}",
        )
        self.discord_id_alvo = discord_id

    async def callback(self, interacao: discord.Interaction) -> None:
        if not isinstance(interacao.user, discord.Member):
            await responder_erro(
                interacao,
                titulo="Fora do servidor",
                linhas=["Só funciona dentro do servidor."],
            )
            return
        ok, mensagem = await zerar_segundos_acumulados_excesso(
            self.discord_id_alvo, interacao.user
        )
        if ok:
            await responder_sucesso(
                interacao,
                titulo="Acumulado limpo",
                linhas=[mensagem],
            )
        else:
            await responder_erro(
                interacao,
                titulo="Não limpou",
                linhas=[mensagem],
            )


class BotaoReanalisar(discord.ui.Button):
    def __init__(self, discord_id: int) -> None:
        super().__init__(
            label="Reanalisar",
            style=discord.ButtonStyle.secondary,
            custom_id=f"fin:reanalisar:{discord_id}",
        )
        self.discord_id_alvo = discord_id

    async def callback(self, interacao: discord.Interaction) -> None:
        await interacao.response.defer(ephemeral=True)
        try:
            view = await montar_painel_membro(interacao, self.discord_id_alvo)
            await interacao.followup.send(view=view, ephemeral=True)
        except Exception as erro_re:
            registrador.exception(
                "Falha ao reanalisar membro %s: %s",
                self.discord_id_alvo,
                erro_re,
            )
            await interacao.followup.send(
                content="Não consegui reanalisar agora.",
                ephemeral=True,
            )


class BotaoVoltarHub(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Voltar ao hub",
            style=discord.ButtonStyle.secondary,
            custom_id="fin:voltar_hub",
        )

    async def callback(self, interacao: discord.Interaction) -> None:
        if not isinstance(interacao.user, discord.Member):
            await responder_erro(
                interacao,
                titulo="Fora do servidor",
                linhas=["Só funciona dentro do servidor."],
            )
            return
        view = PainelFinanceiroHub(interacao.user)
        await responder_view(interacao, view, ephemeral=True)


class PainelConfirmacaoRecalculo(LoggingViewMixin, discord.ui.LayoutView):
    def __init__(
        self,
        administrador: discord.Member,
        discord_id: int,
        simulacao_texto: str,
        saldo_sugerido: int,
        delta: int,
    ):
        super().__init__(timeout=300)
        self.administrador_id = administrador.id
        self.discord_id_alvo = discord_id
        self.saldo_sugerido = saldo_sugerido
        self.delta = delta

        linha = discord.ui.ActionRow()
        botao_ok = discord.ui.Button(
            label="Confirmar correção",
            style=discord.ButtonStyle.danger,
            custom_id=f"fin:confirma_recalc:{discord_id}",
        )

        async def ao_confirmar(interacao: discord.Interaction) -> None:
            if not await _checar_dono_painel(interacao, self.administrador_id):
                return
            await interacao.response.send_modal(
                ModalConfirmarRecalculo(
                    self.discord_id_alvo,
                    self.saldo_sugerido,
                    self.delta,
                )
            )

        botao_ok.callback = ao_confirmar  # type: ignore[method-assign]
        linha.add_item(botao_ok)

        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(
                    f"# 🧮 Recálculo — <@{discord_id}>\n"
                    f"{simulacao_texto}\n\n"
                    "Confirmar abre um modal de motivo obrigatório e "
                    "aplica o saldo sugerido."
                ),
                linha,
            )
        )

    async def interaction_check(
        self, interacao: discord.Interaction
    ) -> bool:
        return await _checar_dono_painel(interacao, self.administrador_id)


class PainelMembroFinanceiro(LoggingViewMixin, discord.ui.LayoutView):
    def __init__(
        self,
        administrador: discord.Member,
        discord_id: int,
        resumo: ResumoCarteiraMembro | None,
        analise: AnaliseCompletaMembro,
    ):
        super().__init__(timeout=600)
        self.administrador_id = administrador.id
        self.discord_id_alvo = discord_id

        corpo_carteira = (
            _linhas_carteira(resumo)
            if resumo is not None
            else "> Sem linha em `estado_plantao`."
        )
        corpo_analise = _linhas_analise(analise)
        saldo = resumo.saldo_moedas if resumo is not None else analise.saldo_atual

        linha1 = discord.ui.ActionRow()
        linha1.add_item(BotaoAjustarSaldo(discord_id, saldo))
        linha1.add_item(BotaoRecalcular(discord_id))
        linha1.add_item(BotaoLimparAcumulado(discord_id))

        linha2 = discord.ui.ActionRow()
        linha2.add_item(BotaoBaixarYaml(discord_id))
        linha2.add_item(BotaoReanalisar(discord_id))
        linha2.add_item(BotaoVoltarHub())

        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(
                    f"# 🧾 Membro <@{discord_id}>\n"
                    f"**Carteira e estado**\n{corpo_carteira}\n\n"
                    f"**Análise completa (histórico inteiro)**\n{corpo_analise}"
                ),
                linha1,
                linha2,
                discord.ui.TextDisplay(
                    "-# Ajuste e recálculo exigem motivo · YAML tem logs + extrato"
                ),
            )
        )

    async def interaction_check(
        self, interacao: discord.Interaction
    ) -> bool:
        return await _checar_dono_painel(interacao, self.administrador_id)


async def montar_painel_membro(
    interacao: discord.Interaction,
    discord_id: int,
) -> PainelMembroFinanceiro:
    if not isinstance(interacao.user, discord.Member):
        raise TypeError("interacao.user precisa ser Member")
    resumo = await buscar_estado_e_carteira(discord_id)
    analise = await analisar_membro_completo(discord_id)
    return PainelMembroFinanceiro(
        administrador=interacao.user,
        discord_id=discord_id,
        resumo=resumo,
        analise=analise,
    )


async def abrir_painel_financeiro(interacao: discord.Interaction) -> None:
    """Ponto de entrada do comando /plantao painel-financeiro."""
    if not isinstance(interacao.user, discord.Member):
        await responder_erro(
            interacao,
            titulo="Fora do servidor",
            linhas=["Só funciona dentro do servidor."],
        )
        return
    try:
        await carregar_regras_do_banco()
    except Exception as erro_regras:
        registrador.exception(
            "Não carregou regras do banco (usando cache/config): %s",
            erro_regras,
        )
    view = PainelFinanceiroHub(interacao.user)
    await responder_view(interacao, view, ephemeral=True)
