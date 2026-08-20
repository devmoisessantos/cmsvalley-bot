"""
Execução do wipe configurado pelo assistente.

1. Backup (obrigatório)
2. Expulsar membros comuns
3. Recriar canais de texto escolhidos
4. Relatório + JSON de novos IDs para config.py

Não mexe em cargos, categorias, painéis não marcados nem no banco.
"""

from __future__ import annotations

import json
import logging
from datetime import (
    datetime,
    timezone,
)
from pathlib import Path

from discord import (
    Guild,
    Member,
)

from src.config import BACKUP_DIR
from src.database.conexao import async_session
from src.database.models import RegistroWipe
from src.wipe.wipe_backup_service import (
    criar_e_salvar_backup_do_wipe,
    montar_nome_da_temporada,
)
from src.wipe.wipe_estrutura_service import recriar_canais_escolhidos
from src.wipe.wipe_logger import publicar_relatorio_do_wipe
from src.wipe.wipe_membros_service import (
    expulsar_membros_comuns,
    listar_preservados_e_expulsaveis,
    nomes_cargos_de_gestao_do_membro,
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


def salvar_json_de_config(
    guilda_id: int,
    temporada: str,
    mapa_config: dict[str, int],
) -> str:
    """Grava JSON com chaves do config.py e novos IDs."""
    pasta = Path(BACKUP_DIR) / str(guilda_id)
    pasta.mkdir(parents=True, exist_ok=True)
    caminho = pasta / f"wipe_config_{temporada}.json"
    payload = {
        "temporada": temporada,
        "gerado_em": datetime.now(timezone.utc).isoformat(),
        "aviso": (
            "Substitua no config.py os valores pelos IDs abaixo. "
            "Chaves com CANAIS_PLANTAO. referem-se a esse dicionário."
        ),
        "novos_ids": mapa_config,
    }
    caminho.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return str(caminho)


async def executar_wipe(
    guilda: Guild,
    iniciador: Member,
    ids_canais_para_recriar: set[int],
    caminho_backup_ja_feito: str | None = None,
) -> EstadoDoWipe:
    """Roda o wipe com as escolhas do assistente."""
    atual = obter_estado_do_wipe()
    if atual is not None and atual.em_andamento:
        raise RuntimeError("Já existe um wipe em andamento.")

    temporada = montar_nome_da_temporada()
    estado = EstadoDoWipe(
        temporada=temporada,
        iniciador_id=iniciador.id,
        iniciador_nome=str(iniciador),
        fase="backup",
        iniciado_em=datetime.now(timezone.utc),
        em_andamento=True,
    )
    definir_estado_do_wipe(estado)

    try:
        _anotar(estado, f"Temporada: {temporada}")
        _anotar(estado, f"Iniciado por: {iniciador} ({iniciador.id})")

        estado.fase = "backup"
        if caminho_backup_ja_feito:
            estado.caminho_backup = caminho_backup_ja_feito
            _anotar(estado, f"Reutilizando backup: {caminho_backup_ja_feito}")
        else:
            _backup, caminho = criar_e_salvar_backup_do_wipe(
                guilda, str(iniciador), temporada
            )
            estado.caminho_backup = caminho
            _anotar(estado, f"Backup salvo: {caminho}")

        preservados, expulsaveis = listar_preservados_e_expulsaveis(guilda)
        for membro in preservados:
            if membro.bot:
                continue
            cargos = nomes_cargos_de_gestao_do_membro(membro)
            _anotar(estado, f"Preservado: {membro} → {cargos}")
        _anotar(estado, f"A expulsar: {len(expulsaveis)}")
        _anotar(estado, f"Canais a recriar: {len(ids_canais_para_recriar)}")

        estado.fase = "expulsar_membros"
        sucessos, falhas, linhas_kick = await expulsar_membros_comuns(
            guilda, f"Wipe de temporada {temporada}"
        )
        estado.membros_expulsos = sucessos
        estado.membros_falha = falhas
        estado.linhas_do_relatorio.extend(linhas_kick[:40])
        _anotar(estado, f"Expulsos: {sucessos} | Falhas: {falhas}")

        estado.fase = "recriar_canais"
        if ids_canais_para_recriar:
            linhas_ch, mapa_cfg, _mapa_ids = await recriar_canais_escolhidos(
                guilda, ids_canais_para_recriar
            )
            estado.linhas_do_relatorio.extend(linhas_ch)
            estado.mapa_config_novos_ids = mapa_cfg
            estado.canais_recriados = sum(
                1 for linha in linhas_ch if linha.startswith("Canal recriado:")
            )
            if mapa_cfg:
                caminho_json = salvar_json_de_config(guilda.id, temporada, mapa_cfg)
                _anotar(estado, f"JSON para config.py: {caminho_json}")
                _anotar(
                    estado,
                    "Novos IDs: " + json.dumps(mapa_cfg, ensure_ascii=False),
                )
        else:
            _anotar(estado, "Nenhum canal marcado para recriar.")

        estado.fase = "concluido"
        estado.em_andamento = False
        _anotar(estado, "Wipe concluído (sem banco/cargos/categorias)")

        await _persistir_registro(estado)
        await publicar_relatorio_do_wipe(
            guilda,
            titulo=f"Wipe temporada {temporada}",
            linhas=estado.linhas_do_relatorio,
        )
        return estado

    except Exception as erro:
        estado.fase = "erro"
        estado.em_andamento = False
        _anotar(estado, f"ERRO FATAL: {erro}")
        registrador.exception("[wipe] falhou: %s", erro)
        await _persistir_registro(estado)
        await publicar_relatorio_do_wipe(
            guilda,
            titulo=f"Wipe FALHOU — {estado.temporada}",
            linhas=estado.linhas_do_relatorio,
        )
        raise
    finally:
        if estado is not None:
            estado.em_andamento = False
            definir_estado_do_wipe(estado)


async def _persistir_registro(estado: EstadoDoWipe) -> None:
    async with async_session() as sessao:
        sessao.add(
            RegistroWipe(
                temporada=estado.temporada,
                iniciador_id=estado.iniciador_id,
                iniciador_nome=estado.iniciador_nome,
                caminho_backup=estado.caminho_backup or "",
                fase_final=estado.fase,
                membros_expulsos=estado.membros_expulsos,
                membros_falha=estado.membros_falha,
                finalizado_em=datetime.now(timezone.utc),
                relatorio="\n".join(estado.linhas_do_relatorio[-200:]),
            )
        )
        await sessao.commit()
