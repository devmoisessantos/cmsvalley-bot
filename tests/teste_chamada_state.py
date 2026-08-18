"""Protege o guarda da chamada ativa para que botões usem a sessão correta."""

from src.plantao.chamada.chamada_state import GuardaDaSessaoAtiva, SessaoChamada


def teste_guarda_da_sessao_ativa_comeca_sem_uma_sessao_definida():
    guarda_da_sessao = GuardaDaSessaoAtiva()

    assert guarda_da_sessao.obter() is None, (
        "Uma nova guarda não pode apontar para uma chamada que não existe."
    )


def teste_guarda_da_sessao_ativa_devolve_a_mesma_sessao_definida():
    guarda_da_sessao = GuardaDaSessaoAtiva()
    sessao_da_chamada = SessaoChamada(
        doutor_id=101,
        chamada_id=202,
        canal_id=303,
    )

    guarda_da_sessao.definir(sessao_da_chamada)

    assert guarda_da_sessao.obter() is sessao_da_chamada, (
        "A guarda deve preservar a referência da sessão ativa para os botões."
    )


def teste_guarda_da_sessao_ativa_limpa_a_sessao_quando_recebe_none():
    guarda_da_sessao = GuardaDaSessaoAtiva()
    sessao_da_chamada = SessaoChamada(
        doutor_id=101,
        chamada_id=202,
        canal_id=303,
    )

    guarda_da_sessao.definir(sessao_da_chamada)
    guarda_da_sessao.definir(None)

    assert guarda_da_sessao.obter() is None, (
        "Passar None deve limpar a sessão quando a chamada terminar."
    )
