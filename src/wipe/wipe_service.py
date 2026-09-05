"""
Orquestra as ações do painel de wipe.
"""

from __future__ import annotations

import logging
from datetime import (
    datetime,
    timezone,
)

from discord import (
    Guild,
    Member,
    Role,
    TextChannel,
)

from src.wipe.wipe_backup_service import (
    criar_backup_forcado_do_banco,
    criar_e_salvar_backup_do_discord,
    montar_nome_da_temporada,
)
from src.wipe.wipe_banco_service import esvaziar_banco_da_temporada
from src.wipe.wipe_estrutura_service import recriar_canal_de_texto
from src.wipe.wipe_logger import publicar_relatorio_do_wipe
from src.wipe.wipe_membros_service import (
    limpar_cargos_e_prefixos,
    listar_preservados_e_comuns,
    remover_cargos_escolhidos_de_todos,
)
from src.wipe.wipe_state import (
    EstadoDoWipe,
    definir_estado_do_wipe,
    obter_estado_do_wipe,
    wipe_esta_em_andamento,
)

registrador = logging.getLogger(__name__)


def _anotar(estado: EstadoDoWipe, linha: str) -> None:
    estado.linhas_do_relatorio.append(linha)
    registrador.info("[wipe] %s", linha)


def _iniciar_estado(iniciador: Member, fase: str) -> EstadoDoWipe:
    if wipe_esta_em_andamento():
        raise RuntimeError("Já existe uma operação de wipe em andamento.")
    estado = EstadoDoWipe(
        temporada=montar_nome_da_temporada(),
        iniciador_id=iniciador.id,
        iniciador_nome=str(iniciador),
        fase=fase,
        iniciado_em=datetime.now(timezone.utc),
        em_andamento=True,
    )
    definir_estado_do_wipe(estado)
    return estado


async def executar_backup_discord(
    guilda: Guild,
    iniciador: Member,
) -> EstadoDoWipe:
    """Só snapshot estrutural do Discord."""
    estado = _iniciar_estado(iniciador, "backup_discord")
    try:
        _anotar(estado, f"Temporada: {estado.temporada}")
        _anotar(estado, f"Iniciado por: {iniciador} ({iniciador.id})")
        _backup, caminho = criar_e_salvar_backup_do_discord(
            guilda, str(iniciador), estado.temporada
        )
        estado.caminho_backup_discord = caminho
        _anotar(estado, f"Backup Discord: {caminho}")
        estado.fase = "concluido"
        estado.em_andamento = False
        await publicar_relatorio_do_wipe(
            guilda,
            titulo=f"Wipe — backup Discord {estado.temporada}",
            linhas=estado.linhas_do_relatorio,
        )
        return estado
    except Exception as erro:
        estado.fase = "erro"
        estado.em_andamento = False
        _anotar(estado, f"ERRO: {erro}")
        await publicar_relatorio_do_wipe(
            guilda,
            titulo=f"Wipe backup Discord FALHOU — {estado.temporada}",
            linhas=estado.linhas_do_relatorio,
        )
        raise
    finally:
        estado.em_andamento = False
        definir_estado_do_wipe(estado)


async def executar_backup_banco_e_esvaziar(
    guilda: Guild,
    iniciador: Member,
) -> EstadoDoWipe:
    """Backup forçado do banco e TRUNCATE das tabelas."""
    estado = _iniciar_estado(iniciador, "backup_banco")
    try:
        _anotar(estado, f"Temporada: {estado.temporada}")
        _anotar(estado, f"Iniciado por: {iniciador} ({iniciador.id})")
        resultado = await criar_backup_forcado_do_banco()
        if not resultado.get("fez_backup") or not resultado.get("caminho"):
            motivo = resultado.get("motivo") or "falha desconhecida"
            raise RuntimeError(f"Backup do banco falhou: {motivo}")
        estado.caminho_backup_banco = resultado["caminho"]
        _anotar(
            estado,
            f"Backup banco: {resultado['caminho']} "
            f"(método: {resultado.get('metodo')})",
        )
        estado.fase = "esvaziar_banco"
        linhas_tabelas = await esvaziar_banco_da_temporada()
        estado.tabelas_esvaziadas = sum(
            1 for linha in linhas_tabelas if linha.startswith("Tabela esvaziada:")
        )
        estado.linhas_do_relatorio.extend(linhas_tabelas)
        estado.fase = "concluido"
        estado.em_andamento = False
        await publicar_relatorio_do_wipe(
            guilda,
            titulo=f"Wipe — backup banco + esvaziar {estado.temporada}",
            linhas=estado.linhas_do_relatorio,
        )
        return estado
    except Exception as erro:
        estado.fase = "erro"
        estado.em_andamento = False
        _anotar(estado, f"ERRO: {erro}")
        await publicar_relatorio_do_wipe(
            guilda,
            titulo=f"Wipe banco FALHOU — {estado.temporada}",
            linhas=estado.linhas_do_relatorio,
        )
        raise
    finally:
        estado.em_andamento = False
        definir_estado_do_wipe(estado)


async def executar_backup_completo(
    guilda: Guild,
    iniciador: Member,
) -> EstadoDoWipe:
    """Discord + banco + esvaziar tabelas."""
    estado = _iniciar_estado(iniciador, "backup_discord")
    try:
        _anotar(estado, f"Temporada: {estado.temporada}")
        _anotar(estado, f"Iniciado por: {iniciador} ({iniciador.id})")
        _backup, caminho = criar_e_salvar_backup_do_discord(
            guilda, str(iniciador), estado.temporada
        )
        estado.caminho_backup_discord = caminho
        _anotar(estado, f"Backup Discord: {caminho}")

        estado.fase = "backup_banco"
        resultado = await criar_backup_forcado_do_banco()
        if not resultado.get("fez_backup") or not resultado.get("caminho"):
            motivo = resultado.get("motivo") or "falha desconhecida"
            raise RuntimeError(
                f"Backup do banco falhou — tabelas NÃO esvaziadas: {motivo}"
            )
        estado.caminho_backup_banco = resultado["caminho"]
        _anotar(estado, f"Backup banco: {resultado['caminho']}")

        estado.fase = "esvaziar_banco"
        linhas_tabelas = await esvaziar_banco_da_temporada()
        estado.tabelas_esvaziadas = sum(
            1 for linha in linhas_tabelas if linha.startswith("Tabela esvaziada:")
        )
        estado.linhas_do_relatorio.extend(linhas_tabelas)
        estado.fase = "concluido"
        estado.em_andamento = False
        await publicar_relatorio_do_wipe(
            guilda,
            titulo=f"Wipe backup completo — {estado.temporada}",
            linhas=estado.linhas_do_relatorio,
        )
        return estado
    except Exception as erro:
        estado.fase = "erro"
        estado.em_andamento = False
        _anotar(estado, f"ERRO: {erro}")
        await publicar_relatorio_do_wipe(
            guilda,
            titulo=f"Wipe backup FALHOU — {estado.temporada}",
            linhas=estado.linhas_do_relatorio,
        )
        raise
    finally:
        estado.em_andamento = False
        definir_estado_do_wipe(estado)


async def executar_limpar_cargos(
    guilda: Guild,
    iniciador: Member,
) -> EstadoDoWipe:
    """Fluxo clássico: preservados vs comuns + prefixos."""
    estado = _iniciar_estado(iniciador, "limpar_cargos")
    try:
        _anotar(estado, f"Temporada: {estado.temporada}")
        _anotar(estado, f"Iniciado por: {iniciador} ({iniciador.id})")
        preservados, comuns = listar_preservados_e_comuns(guilda)
        _anotar(estado, f"Preservados: {len(preservados)}")
        _anotar(estado, f"Comuns: {len(comuns)}")
        motivo = f"Wipe temporada {estado.temporada} — limpar cargos"
        n_pres, n_limpos, n_falhas, linhas = await limpar_cargos_e_prefixos(
            guilda, motivo
        )
        estado.membros_preservados = n_pres
        estado.membros_limpos = n_limpos
        estado.membros_falha = n_falhas
        estado.linhas_do_relatorio.extend(linhas)
        estado.fase = "concluido"
        estado.em_andamento = False
        await publicar_relatorio_do_wipe(
            guilda,
            titulo=f"Wipe limpar-cargos — {estado.temporada}",
            linhas=estado.linhas_do_relatorio,
        )
        return estado
    except Exception as erro:
        estado.fase = "erro"
        estado.em_andamento = False
        _anotar(estado, f"ERRO: {erro}")
        await publicar_relatorio_do_wipe(
            guilda,
            titulo=f"Wipe limpar-cargos FALHOU — {estado.temporada}",
            linhas=estado.linhas_do_relatorio,
        )
        raise
    finally:
        estado.em_andamento = False
        definir_estado_do_wipe(estado)


async def executar_remover_cargos_escolhidos(
    guilda: Guild,
    iniciador: Member,
    cargos: list[Role],
) -> EstadoDoWipe:
    """Remove só os cargos selecionados de todos os membros."""
    estado = _iniciar_estado(iniciador, "remover_cargos_escolhidos")
    try:
        nomes = ", ".join(cargo.name for cargo in cargos)
        _anotar(estado, f"Temporada: {estado.temporada}")
        _anotar(estado, f"Iniciado por: {iniciador} ({iniciador.id})")
        _anotar(estado, f"Cargos alvo: {nomes}")
        motivo = f"Wipe — remover cargos selecionados ({nomes})"
        afetados, falhas, linhas = await remover_cargos_escolhidos_de_todos(
            guilda, cargos, motivo
        )
        estado.membros_limpos = afetados
        estado.membros_falha = falhas
        estado.linhas_do_relatorio.extend(linhas)
        estado.fase = "concluido"
        estado.em_andamento = False
        await publicar_relatorio_do_wipe(
            guilda,
            titulo=f"Wipe remover cargos — {estado.temporada}",
            linhas=estado.linhas_do_relatorio,
        )
        return estado
    except Exception as erro:
        estado.fase = "erro"
        estado.em_andamento = False
        _anotar(estado, f"ERRO: {erro}")
        await publicar_relatorio_do_wipe(
            guilda,
            titulo=f"Wipe remover cargos FALHOU — {estado.temporada}",
            linhas=estado.linhas_do_relatorio,
        )
        raise
    finally:
        estado.em_andamento = False
        definir_estado_do_wipe(estado)


async def executar_recriar_canal(
    guilda: Guild,
    iniciador: Member,
    canal: TextChannel,
) -> tuple[str, EstadoDoWipe]:
    """
    Apaga e recria um canal.

    Devolve (linha NOME: ID ou erro, estado).
    """
    estado = _iniciar_estado(iniciador, "recriar_canal")
    try:
        _anotar(estado, f"Temporada: {estado.temporada}")
        _anotar(estado, f"Iniciado por: {iniciador} ({iniciador.id})")
        _anotar(estado, f"Recriando canal: #{canal.name} ({canal.id})")
        _novo, linha = await recriar_canal_de_texto(canal)
        _anotar(estado, linha)
        estado.fase = "concluido"
        estado.em_andamento = False
        await publicar_relatorio_do_wipe(
            guilda,
            titulo=f"Wipe recriar canal — {estado.temporada}",
            linhas=estado.linhas_do_relatorio,
        )
        return linha, estado
    except Exception as erro:
        estado.fase = "erro"
        estado.em_andamento = False
        _anotar(estado, f"ERRO: {erro}")
        await publicar_relatorio_do_wipe(
            guilda,
            titulo=f"Wipe recriar canal FALHOU — {estado.temporada}",
            linhas=estado.linhas_do_relatorio,
        )
        raise
    finally:
        estado.em_andamento = False
        definir_estado_do_wipe(estado)
