"""
Orquestra as duas operações do wipe:

1. /wipe backup  — snapshot Discord + backup do banco + esvaziar tabelas
2. /wipe limpar-cargos — remover cargos e prefixos (com exceção da diretoria)
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
)

from src.wipe.wipe_backup_service import (
    criar_backup_forcado_do_banco,
    criar_e_salvar_backup_do_discord,
    montar_nome_da_temporada,
)
from src.wipe.wipe_banco_service import esvaziar_banco_da_temporada
from src.wipe.wipe_logger import publicar_relatorio_do_wipe
from src.wipe.wipe_membros_service import (
    limpar_cargos_e_prefixos,
    listar_preservados_e_comuns,
)
from src.wipe.wipe_state import (
    EstadoDoWipe,
    definir_estado_do_wipe,
    obter_estado_do_wipe,
)

registrador = logging.getLogger(__name__)


def _anotar(estado: EstadoDoWipe, linha: str) -> None:
    estado.linhas_do_relatorio.append(linha)
    registrador.info("[wipe] %s", linha)


async def executar_backup_e_esvaziar_banco(
    guilda: Guild,
    iniciador: Member,
) -> EstadoDoWipe:
    """
    1) Snapshot completo do Discord
    2) Backup forçado do PostgreSQL
    3) TRUNCATE de todas as tabelas operacionais

    Só avança para o passo 3 se o backup do banco tiver caminho válido.
    """
    atual = obter_estado_do_wipe()
    if atual is not None and atual.em_andamento:
        raise RuntimeError("Já existe uma operação de wipe em andamento.")

    temporada = montar_nome_da_temporada()
    estado = EstadoDoWipe(
        temporada=temporada,
        iniciador_id=iniciador.id,
        iniciador_nome=str(iniciador),
        fase="backup_discord",
        iniciado_em=datetime.now(timezone.utc),
        em_andamento=True,
    )
    definir_estado_do_wipe(estado)

    try:
        _anotar(estado, f"Temporada: {temporada}")
        _anotar(estado, f"Iniciado por: {iniciador} ({iniciador.id})")
        _anotar(estado, "Fase: backup do Discord")

        _backup, caminho_discord = criar_e_salvar_backup_do_discord(
            guilda, str(iniciador), temporada
        )
        estado.caminho_backup_discord = caminho_discord
        _anotar(estado, f"Backup Discord: {caminho_discord}")

        estado.fase = "backup_banco"
        _anotar(estado, "Fase: backup forçado do banco")
        resultado_banco = await criar_backup_forcado_do_banco()
        if not resultado_banco.get("fez_backup") or not resultado_banco.get("caminho"):
            motivo = resultado_banco.get("motivo") or "falha desconhecida"
            raise RuntimeError(
                f"Backup do banco falhou — tabelas NÃO foram esvaziadas. Motivo: {motivo}"
            )
        estado.caminho_backup_banco = resultado_banco["caminho"]
        _anotar(
            estado,
            f"Backup banco: {resultado_banco['caminho']} "
            f"(método: {resultado_banco.get('metodo')})",
        )

        estado.fase = "esvaziar_banco"
        _anotar(estado, "Fase: esvaziar tabelas do banco")
        linhas_tabelas = await esvaziar_banco_da_temporada()
        estado.tabelas_esvaziadas = sum(
            1 for linha in linhas_tabelas if linha.startswith("Tabela esvaziada:")
        )
        estado.linhas_do_relatorio.extend(linhas_tabelas)
        _anotar(estado, f"Tabelas esvaziadas: {estado.tabelas_esvaziadas}")

        estado.fase = "concluido"
        estado.em_andamento = False
        _anotar(estado, "Backup + esvaziar banco concluídos")

        await publicar_relatorio_do_wipe(
            guilda,
            titulo=f"Wipe backup — temporada {temporada}",
            linhas=estado.linhas_do_relatorio,
        )
        return estado

    except Exception as erro:
        estado.fase = "erro"
        estado.em_andamento = False
        _anotar(estado, f"ERRO: {erro}")
        registrador.exception("[wipe] backup falhou: %s", erro)
        await publicar_relatorio_do_wipe(
            guilda,
            titulo=f"Wipe backup FALHOU — {estado.temporada}",
            linhas=estado.linhas_do_relatorio,
        )
        raise
    finally:
        if estado is not None:
            estado.em_andamento = False
            definir_estado_do_wipe(estado)


async def executar_limpar_cargos(
    guilda: Guild,
    iniciador: Member,
) -> EstadoDoWipe:
    """
    Remove cargos e prefixos de todos os membros.

    Diretoria e responsáveis: mantêm cargos da lista + HP S・Valley.
    Gate e demais: perdem todos os cargos.
    """
    atual = obter_estado_do_wipe()
    if atual is not None and atual.em_andamento:
        raise RuntimeError("Já existe uma operação de wipe em andamento.")

    temporada = montar_nome_da_temporada()
    estado = EstadoDoWipe(
        temporada=temporada,
        iniciador_id=iniciador.id,
        iniciador_nome=str(iniciador),
        fase="limpar_cargos",
        iniciado_em=datetime.now(timezone.utc),
        em_andamento=True,
    )
    definir_estado_do_wipe(estado)

    try:
        _anotar(estado, f"Temporada: {temporada}")
        _anotar(estado, f"Iniciado por: {iniciador} ({iniciador.id})")

        preservados, comuns = listar_preservados_e_comuns(guilda)
        _anotar(estado, f"Preservados (diretoria/área): {len(preservados)}")
        _anotar(estado, f"Comuns a limpar: {len(comuns)}")

        estado.fase = "limpar_cargos"
        motivo = f"Wipe temporada {temporada} — limpar cargos e prefixos"
        n_pres, n_limpos, n_falhas, linhas = await limpar_cargos_e_prefixos(
            guilda, motivo
        )
        estado.membros_preservados = n_pres
        estado.membros_limpos = n_limpos
        estado.membros_falha = n_falhas
        estado.membros_processados = n_pres + n_limpos + n_falhas
        estado.linhas_do_relatorio.extend(linhas)

        _anotar(
            estado,
            f"Resultado: preservados={n_pres} limpos={n_limpos} falhas={n_falhas}",
        )

        estado.fase = "concluido"
        estado.em_andamento = False
        _anotar(estado, "Limpeza de cargos e prefixos concluída")

        await publicar_relatorio_do_wipe(
            guilda,
            titulo=f"Wipe limpar-cargos — temporada {temporada}",
            linhas=estado.linhas_do_relatorio,
        )
        return estado

    except Exception as erro:
        estado.fase = "erro"
        estado.em_andamento = False
        _anotar(estado, f"ERRO: {erro}")
        registrador.exception("[wipe] limpar-cargos falhou: %s", erro)
        await publicar_relatorio_do_wipe(
            guilda,
            titulo=f"Wipe limpar-cargos FALHOU — {estado.temporada}",
            linhas=estado.linhas_do_relatorio,
        )
        raise
    finally:
        if estado is not None:
            estado.em_andamento = False
            definir_estado_do_wipe(estado)
