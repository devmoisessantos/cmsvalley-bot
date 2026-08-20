"""
Assistente configurável do wipe — Components V2.

Fluxo:
1. Início (resumo + exportar backup)
2. Canais a recriar (select de 25 em 25, escolhas gravadas)
3. Revisão dos canais marcados
4. Revisão de expulsões (preservados)
5. Confirmação final (modal WIPE)

Timeout da view: 1 hora. Botões Voltar e Desfazer entre etapas.

Importante: interações de botão usam edit_message / defer sem ephemeral,
para redesenhar o MESMO card do assistente (e não criar outra mensagem).
"""

from __future__ import annotations

import json
import logging
from datetime import (
    datetime,
    timezone,
)
from math import ceil

import discord

from src.utils.mensagens import (
    responder_aviso,
    responder_erro,
    responder_sucesso,
)
from src.wipe.wipe_backup_service import (
    criar_e_salvar_backup_do_wipe,
    montar_nome_da_temporada,
)
from src.wipe.wipe_membros_service import listar_preservados_e_expulsaveis
from src.wipe.wipe_service import executar_wipe
from src.wipe.wipe_state import (
    ETAPA_CANAIS,
    ETAPA_CONFIRMACAO,
    ETAPA_INICIO,
    ETAPA_MEMBROS,
    ETAPA_REVISAO_CANAIS,
    SessaoDoAssistenteWipe,
    definir_sessao_assistente,
    desfazer_ultima_marcacao,
    guardar_marcacao_no_historico,
    limpar_sessao_assistente,
    obter_sessao_assistente,
    wipe_esta_em_andamento,
)

registrador = logging.getLogger(__name__)

TIMEOUT_ASSISTENTE_SEGUNDOS = 3600
CANAIS_POR_PAGINA = 25


def _montar_catalogo_canais(guilda: discord.Guild) -> list[tuple[int, str]]:
    """Lista canais de texto ordenados por categoria e nome."""
    itens: list[tuple[int, str]] = []
    canais_ordenados = sorted(
        guilda.text_channels,
        key=lambda canal: (
            canal.category.position if canal.category else -1,
            canal.position,
            canal.name,
        ),
    )
    for canal in canais_ordenados:
        categoria = canal.category.name if canal.category else "sem categoria"
        rotulo = f"#{canal.name} · {categoria}"[:100]
        itens.append((canal.id, rotulo))
    return itens


class ViewAssistenteWipe(discord.ui.LayoutView):
    """View base do assistente — 1 hora sem interação encerra a sessão."""

    def __init__(self, usuario_id: int):
        super().__init__(timeout=float(TIMEOUT_ASSISTENTE_SEGUNDOS))
        self.usuario_id = usuario_id

    async def on_timeout(self) -> None:
        limpar_sessao_assistente(self.usuario_id)
        registrador.info("[wipe] assistente expirou para usuario %s", self.usuario_id)

    async def interaction_check(self, interacao: discord.Interaction) -> bool:
        if interacao.user.id != self.usuario_id:
            await responder_aviso(
                interacao,
                titulo="Assistente de outro membro",
                linhas=["Só quem abriu o assistente pode usar estes botões."],
            )
            return False
        return True


def _texto_progresso_canais(sessao: SessaoDoAssistenteWipe) -> str:
    total = len(sessao.catalogo_canais)
    paginas = max(1, ceil(total / CANAIS_POR_PAGINA)) if total else 1
    pagina = sessao.pagina_canais + 1
    marcados = len(sessao.ids_canais_para_recriar)
    return (
        f"Página **{pagina}/{paginas}** · "
        f"**{total}** canais de texto · "
        f"**{marcados}** marcados para recriar"
    )


def _fatia_catalogo(
    sessao: SessaoDoAssistenteWipe,
) -> list[tuple[int, str]]:
    inicio = sessao.pagina_canais * CANAIS_POR_PAGINA
    fim = inicio + CANAIS_POR_PAGINA
    return sessao.catalogo_canais[inicio:fim]


def montar_view_da_etapa(
    sessao: SessaoDoAssistenteWipe,
    guilda: discord.Guild | None = None,
) -> ViewAssistenteWipe:
    """Despacha a montagem do card conforme a etapa atual."""
    if sessao.etapa == ETAPA_INICIO:
        return _view_inicio(sessao)
    if sessao.etapa == ETAPA_CANAIS:
        return _view_canais(sessao)
    if sessao.etapa == ETAPA_REVISAO_CANAIS:
        return _view_revisao_canais(sessao)
    if sessao.etapa == ETAPA_MEMBROS:
        return _view_membros(sessao, guilda)
    return _view_confirmacao(sessao, guilda)


def _view_inicio(sessao: SessaoDoAssistenteWipe) -> ViewAssistenteWipe:
    layout = ViewAssistenteWipe(sessao.usuario_id)
    backup_txt = sessao.caminho_backup or "ainda não exportado"
    texto = (
        "## Assistente de wipe de temporada\n"
        f"Temporada sugerida: `{montar_nome_da_temporada()}`\n\n"
        "**O que este wipe faz**\n"
        "• Expulsa membros comuns (diretoria fica)\n"
        "• Recria **só** os canais de texto que você marcar (limpa o chat)\n"
        "• Gera JSON com novos IDs para atualizar o `config.py`\n\n"
        "**O que NÃO faz**\n"
        "• Não apaga cargos\n"
        "• Não apaga categorias\n"
        "• Não mexe no banco de dados\n"
        "• Não apaga canais que você não marcar\n\n"
        f"Backup: `{backup_txt}`\n"
        f"Canais de texto no catálogo: **{len(sessao.catalogo_canais)}**\n\n"
        "-# Você tem **1 hora** para configurar. Use Voltar e Desfazer."
    )

    row = discord.ui.ActionRow()
    botao_backup = discord.ui.Button(
        label="Exportar backup agora",
        style=discord.ButtonStyle.secondary,
    )
    botao_backup.callback = _ao_exportar_backup
    row.add_item(botao_backup)

    botao_seguir = discord.ui.Button(
        label="Escolher canais",
        style=discord.ButtonStyle.primary,
    )
    botao_seguir.callback = _ao_ir_canais
    row.add_item(botao_seguir)

    layout.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay(texto),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            row,
            accent_color=discord.Color.dark_gold(),
        )
    )
    return layout


def _view_canais(sessao: SessaoDoAssistenteWipe) -> ViewAssistenteWipe:
    layout = ViewAssistenteWipe(sessao.usuario_id)
    fatia = _fatia_catalogo(sessao)
    total = len(sessao.catalogo_canais)
    paginas = max(1, ceil(total / CANAIS_POR_PAGINA)) if total else 1
    pode_desfazer = bool(sessao.historico_marcacoes)

    texto = (
        "## Canais a recriar (limpar histórico)\n"
        "Marque os canais cujo **chat deve ser zerado** "
        "(logs, gerais sujos, etc.).\n"
        "Canais de **painel fixo** deixe de fora.\n\n"
        f"{_texto_progresso_canais(sessao)}\n\n"
        "Ao mudar a seleção desta página, as marcações são **salvas**.\n"
        "Use **Desfazer** para voltar à marcação anterior."
    )

    componentes: list = [
        discord.ui.TextDisplay(texto),
        discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
    ]

    if fatia:
        opcoes = []
        for id_canal, rotulo in fatia:
            opcoes.append(
                discord.SelectOption(
                    label=rotulo[:100],
                    value=str(id_canal),
                    default=(id_canal in sessao.ids_canais_para_recriar),
                )
            )
        seletor = discord.ui.Select(
            placeholder="Marque os canais desta página para recriar",
            min_values=0,
            max_values=len(opcoes),
            options=opcoes,
        )
        seletor.callback = _ao_salvar_pagina_canais
        row_sel = discord.ui.ActionRow()
        row_sel.add_item(seletor)
        componentes.append(row_sel)
    else:
        componentes.append(discord.ui.TextDisplay("Nenhum canal de texto encontrado."))

    row_nav = discord.ui.ActionRow()
    botao_voltar = discord.ui.Button(
        label="Voltar",
        style=discord.ButtonStyle.secondary,
    )
    botao_voltar.callback = _ao_voltar_inicio
    row_nav.add_item(botao_voltar)

    botao_desfazer = discord.ui.Button(
        label="Desfazer",
        style=discord.ButtonStyle.secondary,
        disabled=not pode_desfazer,
    )
    botao_desfazer.callback = _ao_desfazer_marcacao
    row_nav.add_item(botao_desfazer)

    botao_ant = discord.ui.Button(
        label="Página anterior",
        style=discord.ButtonStyle.secondary,
        disabled=(sessao.pagina_canais <= 0),
    )
    botao_ant.callback = _ao_pagina_anterior
    row_nav.add_item(botao_ant)

    botao_prox = discord.ui.Button(
        label="Próxima página",
        style=discord.ButtonStyle.secondary,
        disabled=(sessao.pagina_canais >= paginas - 1),
    )
    botao_prox.callback = _ao_pagina_proxima
    row_nav.add_item(botao_prox)
    componentes.append(row_nav)

    row_ok = discord.ui.ActionRow()
    botao_ok = discord.ui.Button(
        label="Revisar canais",
        style=discord.ButtonStyle.primary,
    )
    botao_ok.callback = _ao_ir_revisao_canais
    row_ok.add_item(botao_ok)
    componentes.append(row_ok)

    layout.add_item(
        discord.ui.Container(
            *componentes,
            accent_color=discord.Color.blurple(),
        )
    )
    return layout


def _view_revisao_canais(sessao: SessaoDoAssistenteWipe) -> ViewAssistenteWipe:
    layout = ViewAssistenteWipe(sessao.usuario_id)
    por_id = {item[0]: item[1] for item in sessao.catalogo_canais}
    linhas = [
        f"• {por_id.get(id_c, id_c)}" for id_c in sorted(sessao.ids_canais_para_recriar)
    ]
    if not linhas:
        corpo = "_Nenhum canal marcado — o wipe só expulsará membros._"
    else:
        corpo = "\n".join(linhas[:40])
        if len(linhas) > 40:
            corpo += f"\n… e mais {len(linhas) - 40}"

    texto = (
        "## Revisão — canais a recriar\n"
        f"Total marcado: **{len(sessao.ids_canais_para_recriar)}**\n\n"
        f"{corpo}"
    )

    row = discord.ui.ActionRow()
    botao_voltar = discord.ui.Button(
        label="Voltar aos canais",
        style=discord.ButtonStyle.secondary,
    )
    botao_voltar.callback = _ao_ir_canais
    row.add_item(botao_voltar)

    botao_desfazer = discord.ui.Button(
        label="Desfazer",
        style=discord.ButtonStyle.secondary,
        disabled=not bool(sessao.historico_marcacoes),
    )
    botao_desfazer.callback = _ao_desfazer_marcacao
    row.add_item(botao_desfazer)

    botao_limpar = discord.ui.Button(
        label="Limpar marcações",
        style=discord.ButtonStyle.danger,
    )
    botao_limpar.callback = _ao_limpar_canais
    row.add_item(botao_limpar)

    botao_seguir = discord.ui.Button(
        label="Ver expulsões",
        style=discord.ButtonStyle.primary,
    )
    botao_seguir.callback = _ao_ir_membros
    row.add_item(botao_seguir)

    layout.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay(texto),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            row,
            accent_color=discord.Color.orange(),
        )
    )
    return layout


def _view_membros(
    sessao: SessaoDoAssistenteWipe,
    guilda: discord.Guild | None,
) -> ViewAssistenteWipe:
    layout = ViewAssistenteWipe(sessao.usuario_id)
    if guilda is None:
        texto = "Servidor indisponível."
    else:
        preservados, expulsaveis = listar_preservados_e_expulsaveis(guilda)
        nomes = [f"• {membro}" for membro in preservados if not membro.bot][:25]
        lista = "\n".join(nomes) if nomes else "_(só bots/dono)_"
        texto = (
            "## Revisão — expulsões\n"
            f"**Preservados:** {len(preservados)} "
            f"(diretoria + dono + bot + IDs fixos)\n"
            f"**Serão expulsos:** {len(expulsaveis)}\n\n"
            f"{lista}\n\n"
            "Cargos **não** são apagados. Quem voltar refaz whitelist."
        )

    row = discord.ui.ActionRow()
    botao_voltar = discord.ui.Button(
        label="Voltar",
        style=discord.ButtonStyle.secondary,
    )
    botao_voltar.callback = _ao_ir_revisao_canais
    row.add_item(botao_voltar)

    botao_seguir = discord.ui.Button(
        label="Confirmação final",
        style=discord.ButtonStyle.primary,
    )
    botao_seguir.callback = _ao_ir_confirmacao
    row.add_item(botao_seguir)

    layout.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay(texto),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            row,
            accent_color=discord.Color.red(),
        )
    )
    return layout


def _view_confirmacao(
    sessao: SessaoDoAssistenteWipe,
    guilda: discord.Guild | None,
) -> ViewAssistenteWipe:
    layout = ViewAssistenteWipe(sessao.usuario_id)
    n_exp = "?"
    if guilda is not None:
        _p, exp = listar_preservados_e_expulsaveis(guilda)
        n_exp = str(len(exp))

    texto = (
        "## Confirmação final\n"
        f"• Expulsar cerca de **{n_exp}** membros\n"
        f"• Recriar **{len(sessao.ids_canais_para_recriar)}** canais de texto\n"
        f"• Backup: `{sessao.caminho_backup or 'será gerado na hora'}`\n"
        "• Banco: **intocado**\n"
        "• Cargos/categorias: **intocados**\n\n"
        "Clique no botão e digite **WIPE** para executar."
    )

    row = discord.ui.ActionRow()
    botao_voltar = discord.ui.Button(
        label="Voltar",
        style=discord.ButtonStyle.secondary,
    )
    botao_voltar.callback = _ao_ir_membros
    row.add_item(botao_voltar)

    botao_go = discord.ui.Button(
        label="Executar wipe",
        style=discord.ButtonStyle.danger,
    )
    botao_go.callback = _ao_abrir_modal_wipe
    row.add_item(botao_go)

    layout.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay(texto),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            row,
            accent_color=discord.Color.dark_red(),
        )
    )
    return layout


async def _redesenhar(
    interacao: discord.Interaction,
    sessao: SessaoDoAssistenteWipe,
) -> None:
    """Atualiza o card do assistente (a mensagem que tem os botões)."""
    definir_sessao_assistente(sessao)
    view = montar_view_da_etapa(sessao, interacao.guild)
    if interacao.response.is_done():
        await interacao.edit_original_response(view=view)
    else:
        await interacao.response.edit_message(view=view)


async def _sessao_ou_aviso(
    interacao: discord.Interaction,
) -> SessaoDoAssistenteWipe | None:
    sessao = obter_sessao_assistente(interacao.user.id)
    if sessao is None:
        await responder_aviso(
            interacao,
            titulo="Sessão expirada",
            linhas=[
                "O assistente expirou ou foi encerrado.",
                "Abra de novo com `/moderacao wipe`.",
            ],
        )
        return None
    return sessao


async def _ao_exportar_backup(interacao: discord.Interaction) -> None:
    sessao = await _sessao_ou_aviso(interacao)
    if sessao is None or interacao.guild is None:
        return
    # defer SEM ephemeral: atualiza o card do componente, não cria outra msg
    await interacao.response.defer()
    try:
        _backup, caminho = criar_e_salvar_backup_do_wipe(
            interacao.guild,
            str(interacao.user),
            montar_nome_da_temporada(),
        )
        sessao.caminho_backup = caminho
        definir_sessao_assistente(sessao)
        await interacao.edit_original_response(
            view=montar_view_da_etapa(sessao, interacao.guild)
        )
        await responder_sucesso(
            interacao,
            titulo="Backup exportado",
            linhas=[f"Arquivo salvo em `{caminho}`."],
        )
    except Exception as erro:
        registrador.exception("[wipe] backup: %s", erro)
        await responder_erro(
            interacao,
            titulo="Falha no backup",
            linhas=[str(erro)],
        )


async def _ao_ir_canais(interacao: discord.Interaction) -> None:
    sessao = await _sessao_ou_aviso(interacao)
    if sessao is None:
        return
    sessao.etapa = ETAPA_CANAIS
    await _redesenhar(interacao, sessao)


async def _ao_voltar_inicio(interacao: discord.Interaction) -> None:
    sessao = await _sessao_ou_aviso(interacao)
    if sessao is None:
        return
    sessao.etapa = ETAPA_INICIO
    await _redesenhar(interacao, sessao)


async def _ao_salvar_pagina_canais(interacao: discord.Interaction) -> None:
    sessao = await _sessao_ou_aviso(interacao)
    if sessao is None:
        return
    fatia = _fatia_catalogo(sessao)
    ids_pagina = {item[0] for item in fatia}
    guardar_marcacao_no_historico(sessao)
    sessao.ids_canais_para_recriar -= ids_pagina
    valores = interacao.data.get("values", []) if interacao.data else []
    for valor in valores:
        sessao.ids_canais_para_recriar.add(int(valor))
    await _redesenhar(interacao, sessao)


async def _ao_desfazer_marcacao(interacao: discord.Interaction) -> None:
    sessao = await _sessao_ou_aviso(interacao)
    if sessao is None:
        return
    if not desfazer_ultima_marcacao(sessao):
        await responder_aviso(
            interacao,
            titulo="Nada para desfazer",
            linhas=["Não há alteração de marcação anterior nesta sessão."],
        )
        return
    await _redesenhar(interacao, sessao)


async def _ao_pagina_anterior(interacao: discord.Interaction) -> None:
    sessao = await _sessao_ou_aviso(interacao)
    if sessao is None:
        return
    sessao.pagina_canais = max(0, sessao.pagina_canais - 1)
    await _redesenhar(interacao, sessao)


async def _ao_pagina_proxima(interacao: discord.Interaction) -> None:
    sessao = await _sessao_ou_aviso(interacao)
    if sessao is None:
        return
    total = len(sessao.catalogo_canais)
    max_pag = max(0, ceil(total / CANAIS_POR_PAGINA) - 1)
    sessao.pagina_canais = min(max_pag, sessao.pagina_canais + 1)
    await _redesenhar(interacao, sessao)


async def _ao_ir_revisao_canais(interacao: discord.Interaction) -> None:
    sessao = await _sessao_ou_aviso(interacao)
    if sessao is None:
        return
    sessao.etapa = ETAPA_REVISAO_CANAIS
    await _redesenhar(interacao, sessao)


async def _ao_limpar_canais(interacao: discord.Interaction) -> None:
    sessao = await _sessao_ou_aviso(interacao)
    if sessao is None:
        return
    if sessao.ids_canais_para_recriar:
        guardar_marcacao_no_historico(sessao)
    sessao.ids_canais_para_recriar.clear()
    await _redesenhar(interacao, sessao)


async def _ao_ir_membros(interacao: discord.Interaction) -> None:
    sessao = await _sessao_ou_aviso(interacao)
    if sessao is None:
        return
    sessao.etapa = ETAPA_MEMBROS
    await _redesenhar(interacao, sessao)


async def _ao_ir_confirmacao(interacao: discord.Interaction) -> None:
    sessao = await _sessao_ou_aviso(interacao)
    if sessao is None:
        return
    sessao.etapa = ETAPA_CONFIRMACAO
    await _redesenhar(interacao, sessao)


class ModalConfirmacaoWipe(discord.ui.Modal, title="Confirmar WIPE"):
    """Exige digitar WIPE para não haver clique acidental."""

    texto_confirmacao = discord.ui.TextInput(
        label="Digite WIPE em maiúsculas",
        placeholder="WIPE",
        required=True,
        max_length=10,
    )

    async def on_submit(self, interacao: discord.Interaction) -> None:
        if self.texto_confirmacao.value.strip() != "WIPE":
            await responder_erro(
                interacao,
                titulo="Confirmação inválida",
                linhas=["Digite exatamente WIPE em maiúsculas."],
            )
            return
        if wipe_esta_em_andamento():
            await responder_aviso(
                interacao,
                titulo="Wipe em andamento",
                linhas=["Já existe um wipe rodando. Aguarde terminar."],
            )
            return
        sessao = obter_sessao_assistente(interacao.user.id)
        if sessao is None or interacao.guild is None:
            await responder_aviso(
                interacao,
                titulo="Sessão expirada",
                linhas=["Abra de novo com `/moderacao wipe`."],
            )
            return

        await interacao.response.defer(ephemeral=True)
        ids = set(sessao.ids_canais_para_recriar)
        caminho_backup = sessao.caminho_backup
        limpar_sessao_assistente(interacao.user.id)

        try:
            estado = await executar_wipe(
                interacao.guild,
                interacao.user,
                ids,
                caminho_backup_ja_feito=caminho_backup,
            )
            linhas = [
                f"Temporada: `{estado.temporada}`",
                f"Expulsos: **{estado.membros_expulsos}** "
                f"(falhas: {estado.membros_falha})",
                f"Canais recriados: **{estado.canais_recriados}**",
                f"Backup: `{estado.caminho_backup or '—'}`",
            ]
            if estado.mapa_config_novos_ids:
                linhas.append("Novos IDs para o config.py:")
                linhas.append(
                    "```json\n"
                    + json.dumps(
                        estado.mapa_config_novos_ids,
                        indent=2,
                        ensure_ascii=False,
                    )
                    + "\n```"
                )
            await responder_sucesso(
                interacao,
                titulo="Wipe finalizado",
                linhas=linhas,
                delay=None,
            )
        except Exception as erro:
            registrador.exception("[wipe] execução: %s", erro)
            await responder_erro(
                interacao,
                titulo="Wipe falhou",
                linhas=[str(erro)],
                delay=None,
            )


async def _ao_abrir_modal_wipe(interacao: discord.Interaction) -> None:
    await interacao.response.send_modal(ModalConfirmacaoWipe())


async def abrir_painel_de_confirmacao(interacao: discord.Interaction) -> None:
    """Ponto de entrada do /moderacao wipe — abre o assistente."""
    if interacao.guild is None:
        await responder_erro(
            interacao,
            titulo="Sem servidor",
            linhas=["Use este comando dentro do servidor."],
        )
        return
    if wipe_esta_em_andamento():
        await responder_aviso(
            interacao,
            titulo="Wipe em andamento",
            linhas=[
                "Já existe um wipe em execução.",
                "Use `/moderacao wipe-status`.",
            ],
        )
        return

    sessao = obter_sessao_assistente(interacao.user.id)
    if sessao is None or sessao.guilda_id != interacao.guild.id:
        sessao = SessaoDoAssistenteWipe(
            usuario_id=interacao.user.id,
            guilda_id=interacao.guild.id,
            etapa=ETAPA_INICIO,
            catalogo_canais=_montar_catalogo_canais(interacao.guild),
            criada_em=datetime.now(timezone.utc),
        )
        definir_sessao_assistente(sessao)

    # interacao já veio com defer do comando — envia o card como followup
    await interacao.followup.send(
        view=montar_view_da_etapa(sessao, interacao.guild),
        ephemeral=True,
    )
