# AGENTS.md — CMS Valley Bot

> **LEIA ANTES DE QUALQUER COISA**
>
> Este arquivo é a constituição deste projeto. Toda alteração, criação ou
> remoção de código DEVE seguir as regras aqui. Se houver conflito entre este
> documento e qualquer outro material, este documento vence.
>
> **Regra de Ouro:** código em português, legível como uma carta para uma
> criança de 10 anos, Components V2, respostas centralizadas em `mensagens.py`,
> organização por domínio, reutilizar o que já existe, e **nunca quebrar o que
> já funciona**.

---

## 1. O que é este projeto

Bot Discord oficial do servidor **CMS Valley**, uma comunidade de roleplay
hospitalar. O bot gerencia plantões, recrutamentos, punições, whitelist,
hierarquia, backups e outros sistemas do servidor.

**Usuários do bot:**

- **Membros comuns** — usam painéis para bater ponto, ver plantões, consultar
  informações
- **Equipe médica** — usa chamados, escalas e registros
- **Administração** — gerencia punições, recrutamentos, whitelist e
  configurações

---

## 2. Stack tecnológica

| Tecnologia | Versão | Uso |
|------------|--------|-----|
| Python | 3.10+ | Linguagem principal |
| discord.py | 2.7 (Components V2) | Framework do Discord |
| SQLAlchemy | 2.0 | ORM |
| PostgreSQL | 14+ | Banco de dados |
| asyncpg / psycopg2 | — | Drivers PostgreSQL |
| python-dotenv | 1.x | Variáveis de ambiente |
| pytest / pytest-asyncio | — | Testes automatizados |
| ruff | — | Formatação e verificação |

**Entrada:** `main.py` → `src/bot.py`

- Não adicionar dependência nova sem justificativa clara
- Preferir bibliotecas assíncronas
- Manter `requirements.txt` com versões exatas

---

## 3. Estrutura de diretórios

Cada assunto do servidor é um **domínio**: uma pasta em `src/` com nome em
português, no singular, sem acentos.

```
src/
├── bot.py                  # cria o cliente e carrega os cogs
├── config.py               # TODA configuração e TODO id do Discord
├── database/               # models, conexao, migracoes
├── utils/                  # código compartilhado entre domínios
└── {dominio}/              # um por assunto: plantao, punicoes, cursos, ...
    ├── {dominio}_cogs.py
    ├── {dominio}_panel.py
    ├── {dominio}_service.py
    ├── {dominio}_logger.py
    └── {subdominio}/       # quando o domínio cresce demais
```

**Regras:**

- Todo recurso novo nasce dentro de um domínio
- Subdomínios são permitidos quando o domínio cresce (ex.: `plantao/chamada/`)
- Não criar código novo em pastas genéricas `panels/`, `cogs/` ou `services/`
- `utils/` é só para código transversal, usado por vários domínios
- Um domínio não importa o miolo de outro domínio; se precisar, o código
  compartilhado sobe para `utils/`

### Padrão de nome de arquivo

| Sufixo | Responsabilidade |
|--------|------------------|
| `_cogs.py` | Comandos e listeners registrados no bot |
| `_panel.py` / `_views.py` | Painéis e componentes (Components V2) |
| `_service.py` | Regra de negócio e acesso ao banco |
| `_logger.py` | Log visual em canal do Discord |
| `_tasks.py` | Tarefas periódicas |
| `_listener.py` | Reação a eventos do Discord |

**Sobre `_logger.py`:** ele é exigido **apenas nos domínios que publicam log
visual em canal do Discord**. Criar arquivos `_logger.py` vazios em domínios que
não postam log seria cerimônia sem função. Hoje oito domínios têm log visual.

---

## 4. Idioma e nomenclatura

### Tudo em português

Funções, variáveis, classes, métodos, comentários, docstrings, mensagens de log
e mensagens de commit — **tudo em português**. Nomes descritivos e longos o
suficiente para serem lidos em voz alta e entendidos sem contexto adicional.

### Proibido

```python
r = await sessao.execute(...)            # "r" não diz nada
m = interaction.user                     # "m" não diz nada
v = CardView(...)                        # "v" não diz nada
msg = await interaction.response...      # "msg" é abreviação
ctx = ...                                # "ctx" é abreviação
e = excecao                              # "e" não diz nada
```

### Obrigatório

```python
resultado_da_consulta = await sessao.execute(...)
membro_que_clicou = interaction.user
view_do_card = CardView(...)
mensagem_resposta = await interaction.response...
contexto_do_comando = ...
erro_capturado = excecao
```

### Tabela de alinhamento

| Português | Conceito em inglês |
|-----------|-------------------|
| cargo | role |
| membro | member |
| painel | panel / view |
| servico | service |
| consulta | query / result |
| interacao | interaction |
| canal | channel |
| servidor / guilda | guild |
| mensagem | message |
| botao | button |
| selecao | select |
| linha de acao | action row |
| comando | command |
| pagina | page |
| verificacao | check |
| validacao | validation |
| registro | register / record |
| atualizacao | update |
| exclusao | delete |
| busca | search / find |
| formulario | form |
| sessao | session |
| conexao | connection |
| transacao | transaction |

**Exceção documentada:** nomes que vêm da API do Discord (`interaction`,
`on_submit`, `setup`, `cog_unload`) ficam como são, porque quem chama é a
biblioteca. O guardião conhece essa lista.

---

## 5. Legibilidade — a carta para uma criança

> Imagine que você está escrevendo uma carta para uma criança de 10 anos que
> começou a aprender programação. Cada linha deve ser compreensível sem esforço.

1. **Uma ideia por linha** — não empilhar operações
2. **Cada linha é uma frase** — ler em voz alta deve fazer sentido
3. **Clareza acima de brevidade** — código longo e claro vence código curto e
   confuso
4. **Sem lógica compacta** — nada de ternário complexo, comprehension aninhada
   ou lambda obscura
5. **Quebrar expressão longa** — usar variável intermediária com nome descritivo
6. **`if`/`else` explícito** — evitar padrão "esperto" que economiza linha e
   confunde
7. **Máximo 88 colunas** por linha
8. **Máximo 60 linhas** por função

### Proibições absolutas

| Proibido | Por quê |
|----------|---------|
| walrus `:=` | Confunde quem está aprendendo |
| `functools`, `itertools` | Ferramentas de "código esperto" |
| `map`, `filter`, `reduce` | Use `for` explícito |
| Comprehension aninhada | Use `for` de verdade |
| `*args`, `**kwargs` | Esconde o que a função recebe |
| `global` | Estado escondido; use uma classe guardiã |
| `eval`, `exec`, `getattr` dinâmico | Impossível de acompanhar |
| `except: pass` silencioso | Esconde defeito |
| SQL escrito à mão | Use o ORM |

---

## 6. Components V2

O projeto usa **Components V2** do discord.py. Isso significa `LayoutView`,
`Container`, `TextDisplay`, `ActionRow` e `Section`.

- **Nunca** criar `discord.Embed` novo
- **Nunca** criar `discord.ui.View` clássico novo
- Botões e selects vivem dentro de um `ActionRow`, dentro de um `Container`
- Texto vive num `TextDisplay` dentro do container

**Pegadinha importante:** `LayoutView` **não** é subclasse de `View`, e uma
mensagem com `LayoutView` **não aceita** `content=` ao mesmo tempo. O texto tem
que ir para dentro do card, num `TextDisplay`.

**Exceção documentada:** `src/financas/financas_views.py` mantém uma `View`
clássica só para reconhecer cliques em mensagens antigas, já publicadas no
Discord. Migrar aquele arquivo quebraria os botões dessas mensagens.

---

## 7. Respostas ao usuário

**Toda** resposta ao membro passa por `src/utils/mensagens.py`. Nada de
`interaction.response.send_message(...)` solto no meio de um serviço.

```python
# Proibido
await interaction.response.send_message("Deu erro", ephemeral=True)

# Obrigatório
await responder_erro(interaction, "Não encontrei seu registro de plantão.")
```

Isso existe para que o visual das respostas seja igual em todo o bot, e para que
mudar o estilo de um aviso seja mudar um arquivo, não trezentos.

**Exceção documentada:** `src/backup/backup_cogs.py` responde direto porque
precisa enviar um arquivo anexado, coisa que o `mensagens.py` não cobre.

---

## 8. Organização por domínio

Ao criar um recurso, pergunte: *de que assunto do servidor isso trata?* A
resposta é a pasta. Se não existe pasta para o assunto, crie uma seguindo o
padrão de arquivos.

Não crie um domínio para uma função só. Não empurre uma função nova para dentro
de um domínio que não tem nada a ver com ela.

---

## 9. Reutilização de código

Antes de escrever qualquer função utilitária, procure em `src/utils/`:

| Arquivo | O que já existe lá |
|---------|-------------------|
| `mensagens.py` | Toda resposta ao usuário |
| `formatacao.py` | Datas, horas, valores em reais, menções de cargo |
| `permissoes.py` | Verificação de quem pode usar o quê |
| `views.py` | Componentes reaproveitados, como confirmação |
| `decoradores.py` | Decoradores compartilhados |

Se você está escrevendo a segunda cópia de algo, pare e suba para `utils/`.

---

## 10. Docstrings e comentários

- **Todo módulo** começa com docstring explicando para que ele serve
- **Toda função pública** tem docstring
- A docstring explica o **porquê**, não repete o nome da função
- Comentário serve para explicar decisão, não para narrar o código

```python
def calcular_horas_do_ciclo(registros):
    """
    Soma as horas de plantão do ciclo atual.

    Só conta registro fechado: um plantão em andamento entraria com duração
    parcial e o ranking mudaria de posição a cada minuto.
    """
```

---

## 11. Tratamento de erros

Todo trecho que pode falhar tem `try` / `except` com **três** coisas:

1. `except` dizendo qual erro
2. Log com contexto suficiente para investigar depois
3. Resposta amigável ao membro

Em operação de banco, sempre `rollback` no erro.

```python
try:
    await sessao.commit()
except SQLAlchemyError as erro_do_banco:
    await sessao.rollback()
    registrador.exception("Falha ao salvar o plantão: %s", erro_do_banco)
    await responder_erro(interaction, "Não consegui salvar seu plantão agora.")
```

**Proibido:** `except: pass`, `except Exception: pass`, e `except` sem log.

---

## 12. Configuração centralizada

**Nenhum id do Discord no meio do código.** Todo id de canal, cargo, categoria
ou servidor mora em `src/config.py`.

```python
# Proibido
canal = guilda.get_channel(1486369153349582990)

# Obrigatório
canal = guilda.get_channel(CANAIS["CANAL_CHAMADAS_HP_SUL"])
```

Segredos (token, endereço do banco) vêm de variável de ambiente, nunca do
código. O `.env.example` documenta cada variável.

---

## 13. Banco de dados

- Tabelas em `src/database/models.py`, com SQLAlchemy
- Sessão e conexão em `src/database/conexao.py`
- **Nunca** SQL escrito à mão; use o ORM
- Sempre `rollback` quando a transação falha

### Migrações

Mudança de estrutura entra como migração numerada em
`src/database/migracoes.py`:

```python
Migracao(
    numero=4,
    descricao="Explique o que a migração faz e por quê",
    comandos_sql=[...],
)
```

O bot guarda a última migração aplicada na tabela `migracoes_aplicadas` e roda
só as que faltam, sozinho, ao ligar.

**Nunca edite uma migração já publicada** — crie a próxima. Editar uma migração
antiga deixa os bancos que já a aplicaram diferentes dos que não aplicaram.

---

## 14. Logs do sistema

Dois tipos de log, com finalidades diferentes:

- **Log técnico** — via `logging`, para quem investiga defeito. Nunca `print()`.
- **Log visual** — card publicado em canal do Discord, para a administração
  acompanhar. Fica no `{dominio}_logger.py`.

**Exceção documentada:** `deploy_logger.py` usa `print()` porque roda antes de o
sistema de log existir.

---

## 15. Checklist antes de qualquer alteração

```bash
python -m ruff format src
python -m ruff check src --select F,E9
python -m pytest tests -q
python ferramentas/guardiao.py
```

Confira também, a olho:

- [ ] Nomes em português e descritivos
- [ ] Uma ideia por linha
- [ ] Nenhum `Embed` ou `View` clássico novo
- [ ] Resposta ao usuário via `mensagens.py`
- [ ] Nenhum id do Discord solto
- [ ] `try`/`except` com log e resposta amigável
- [ ] Docstring no módulo e nas funções públicas
- [ ] Código no domínio certo
- [ ] Nada que já existe em `utils/` foi reescrito

### O guardião

`ferramentas/guardiao.py` verifica as regras deste documento automaticamente.

```bash
python ferramentas/guardiao.py                     # placar completo
python ferramentas/guardiao.py --regra linha-longa # detalha uma regra
python ferramentas/guardiao.py --tudo              # todos os achados
```

Ele separa as regras em **obrigatórias** (reprovam a mudança) e **pendências
conhecidas** (aparecem no placar, não reprovam). As pendências estão descritas
em `DIVIDA_TECNICA.md`. Quando uma pendência é resolvida, tire a regra de
`REGRAS_QUE_AINDA_NAO_DERRUBAM` para que ela passe a reprovar regressões.

As exceções documentadas citadas neste arquivo estão declaradas no guardião, com
o motivo escrito ao lado. Exceção sem motivo escrito não entra.

### Os testes

`tests/` tem testes que rodam sem banco e sem Discord. Eles conferem formatação,
permissões, migrações, configuração, o estado da chamada, e a arquitetura (por
exemplo: que um domínio não importa o miolo de outro).

---

## 16. Processo de commit

Mensagem de commit em português, no formato:

```
src: {ação no gerúndio} em {caminho}
```

Exemplos:

```
src: centralizando respostas em src/plantao/plantao_service.py
src: corrigindo cálculo de horas em src/plantao/carteira_service.py
src: extraindo log visual para src/cursos/cursos_logger.py
```

Um commit trata de um assunto. Não misture renomear arquivo com mudar regra de
negócio.

---

## 17. Como responder e trabalhar neste projeto

- Explicar em português claro, sem jargão desnecessário
- Mostrar **antes → depois** quando altera código
- Ser didático: dizer *por que* a mudança é melhor, não só *que* mudou
- Clareza acima de brevidade
- Sem emojis nas respostas
- Linhas de até 88 colunas, inclusive na conversa

---

## 18. Ordem de melhoria do projeto

Quando houver dívida acumulada, ataque nesta ordem:

1. **Contenção de risco** — o que pode quebrar em produção
2. **Fundação** — configuração, banco, utilitários compartilhados
3. **Respostas e visual** — centralização no `mensagens.py`, Components V2
4. **Erros e logs** — `except` silencioso, `print()` solto
5. **Nomes e legibilidade** — nomes proibidos, docstrings, funções longas
6. **Estrutura** — mover arquivo, criar subdomínio, migrações
7. **Qualidade** — testes, verificador, documentação

Sempre **um domínio por vez**. Migração gradual. Rode o checklist entre cada
passo.

---

## 19. Resumo final

| Mandamento | Regra |
|-----------|-------|
| 1 | Português sempre — nomes, comentários, docstrings, commits |
| 2 | Nomes descritivos — proibido `r`, `m`, `v`, `msg`, `ctx`, `e` |
| 3 | Uma ideia por linha |
| 4 | Components V2 — nunca `Embed` novo, nunca `View` clássico novo |
| 5 | Respostas via `mensagens.py` |
| 6 | Organização por domínio |
| 7 | Reutilizar o que já existe |
| 8 | Tratar erros — `except` nomeado, log, resposta amigável, `rollback` |
| 9 | Configuração centralizada — nenhum id solto |
| 10 | **Nunca quebrar o que já funciona** — migração gradual, um domínio por vez |

O décimo mandamento vence os outros nove. Se seguir uma regra exige mudar o
miolo de código que hoje funciona sem que haja como testar o resultado, registre
a dívida em `DIVIDA_TECNICA.md` e siga em frente. Dívida medida e escrita é
melhor que conserto às cegas.
