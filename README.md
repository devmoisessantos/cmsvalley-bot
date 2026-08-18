# CMS Valley Bot

Bot Discord oficial do servidor **CMS Valley**, uma comunidade de roleplay
hospitalar. O bot cuida de plantões, chamadas, recrutamento, cursos, punições,
whitelist, hierarquia, baú, finanças, tickets e backups.

- **Linguagem:** Python 3.10 ou mais novo
- **Framework:** discord.py 2.7 com Components V2
- **Banco:** PostgreSQL 14 ou mais novo, acessado por SQLAlchemy 2 (assíncrono)
- **Tamanho:** 175 módulos, 34 cogs, cerca de 93 comandos, 24 domínios

Todo o código é escrito em português e segue a constituição do projeto, que está
em [`AGENTS.md`](AGENTS.md). Leia esse arquivo antes de escrever qualquer linha.

---

## Como rodar na sua máquina

### 1. Preparar o ambiente

```bash
python -m venv venv
source venv/bin/activate          # no Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Preencher a configuração

```bash
cp .env.example .env
```

Abra o `.env` e preencha os valores. Os três obrigatórios são:

| Variável | Para que serve |
|----------|----------------|
| `DISCORD_TOKEN` | Token do bot, pego no Discord Developer Portal |
| `GUILD_ID` | ID do servidor onde os comandos são sincronizados |
| `DATABASE_URL` | Endereço do PostgreSQL, no formato `postgresql+asyncpg://usuario:senha@servidor:porta/banco` |

O arquivo `.env.example` explica todas as outras variáveis, uma por uma.

### 3. Ligar o bot

```bash
python main.py
```

Na primeira vez, o bot cria as tabelas que faltam e aplica as migrações
pendentes automaticamente. Você não precisa rodar nada à mão.

### 4. Permissões no Discord

Na aba **Bot** do Developer Portal, ligue os *Privileged Gateway Intents*:

- `SERVER MEMBERS INTENT`
- `MESSAGE CONTENT INTENT`

No convite (OAuth2 > URL Generator), use os escopos `bot` e
`applications.commands`, com as permissões `Manage Roles`, `Manage Channels`,
`Manage Nicknames`, `View Channels`, `Send Messages`, `Attach Files` e
`Embed Links`.

---

## Como o projeto está organizado

O código é dividido **por domínio**: cada assunto do servidor mora na sua
própria pasta, e cada pasta tem sempre os mesmos tipos de arquivo.

```
cmsvalley-bot/
├── main.py                     # ponto de entrada; só chama src/bot.py
├── src/
│   ├── bot.py                  # cria o cliente e carrega os 34 cogs
│   ├── config.py               # TODA configuração e TODO id do Discord
│   ├── database/
│   │   ├── models.py           # tabelas (SQLAlchemy)
│   │   ├── conexao.py          # sessão do banco e migrações
│   │   └── migracoes.py        # migrações numeradas
│   ├── utils/                  # código compartilhado entre domínios
│   │   ├── mensagens.py        # TODA resposta ao usuário passa por aqui
│   │   ├── formatacao.py       # datas, valores, menções
│   │   ├── permissoes.py       # quem pode usar o quê
│   │   └── views.py            # componentes reaproveitados
│   ├── plantao/                # exemplo de domínio completo
│   │   ├── plantao_cogs.py     # comandos
│   │   ├── plantao_panel.py    # painéis (Components V2)
│   │   ├── plantao_service.py  # regra de negócio e banco
│   │   ├── plantao_logger.py   # logs visuais em canal do Discord
│   │   └── chamada/            # subdomínio, quando o domínio cresce
│   └── ...                     # ausencia, avaliacao, backup, bau, cursos,
│                               # demissao, financas, gate, guia, hierarquia,
│                               # laudos, manutencao, membros, notificacoes,
│                               # promocoes, punicoes, recrutamento, templates,
│                               # tickets, utilidade, whitelist
├── tests/                      # 31 testes automatizados
├── ferramentas/
│   └── guardiao.py             # verifica se o código segue o AGENTS.md
├── requirements.txt
└── AGENTS.md                   # a constituição do projeto
```

### O padrão de arquivos de cada domínio

| Sufixo | Responsabilidade |
|--------|------------------|
| `_cogs.py` | Comandos e listeners que o Discord chama |
| `_panel.py` / `_views.py` | Painéis e componentes visuais (Components V2) |
| `_service.py` | Regra de negócio e conversa com o banco |
| `_logger.py` | Log visual em canal do Discord (só nos domínios que têm) |
| `_tasks.py` | Tarefas que rodam sozinhas, de tempo em tempo |
| `_listener.py` | Reação a eventos do Discord |

---

## Antes de enviar qualquer mudança

Rode estes quatro comandos, nesta ordem. Se algum reclamar, conserte antes de
seguir.

```bash
# 1. Formata e procura erro de sintaxe e import morto
python -m ruff format src
python -m ruff check src --select F,E9

# 2. Roda os 31 testes automatizados
python -m pytest tests -q

# 3. Verifica se o código respeita o AGENTS.md
python ferramentas/guardiao.py
```

### O guardião

`ferramentas/guardiao.py` é um verificador escrito para este projeto. Ele lê
todo o código e avisa quando alguma regra do `AGENTS.md` foi quebrada.

```bash
python ferramentas/guardiao.py                    # placar completo
python ferramentas/guardiao.py --regra linha-longa # detalha uma regra só
python ferramentas/guardiao.py --tudo             # mostra todos os achados
```

Ele separa as regras em dois grupos:

- **Regras obrigatórias** — se alguma falhar, o guardião reprova a mudança.
  Exemplos: `discord.Embed` novo, `print()` solto, `except` silencioso,
  resposta direta ao usuário sem passar pelo `mensagens.py`, id do Discord
  escrito no meio do código, nome de variável abreviado.
- **Pendências conhecidas** — dívidas já mapeadas, que aparecem no placar mas
  não reprovam. Estão listadas em [`DIVIDA_TECNICA.md`](DIVIDA_TECNICA.md).

### Os testes

Os 31 testes não precisam de banco nem de conexão com o Discord. Eles conferem
formatação, permissões, migrações, configuração, o estado da chamada e a
arquitetura (por exemplo: nenhum domínio importa outro domínio por dentro).

---

## Banco de dados e migrações

As tabelas são criadas na primeira execução. Depois disso, qualquer mudança de
estrutura entra como uma **migração numerada** em `src/database/migracoes.py`:

```python
Migracao(
    numero=4,
    descricao="Explique aqui o que a migração faz e por quê",
    comandos_sql=[...],
)
```

O bot guarda o número da última migração aplicada na tabela
`migracoes_aplicadas` e roda somente as que faltam. Nunca edite uma migração
já publicada — crie a próxima.

---

## Backups

O bot faz dois tipos de backup automático:

- **Cargos e canais do servidor**, em JSON, a cada `AUTO_BACKUP_INTERVAL_HOURS`
  horas (padrão 12).
- **Banco de dados**, a cada `AUTO_BACKUP_DB_INTERVAL_MINUTES` minutos
  (padrão 360).

Os arquivos ficam na pasta indicada por `BACKUP_DIR`. Se você hospeda o bot em
um serviço que apaga o disco a cada publicação, use um disco persistente — sem
isso os backups desaparecem.

**Limite do próprio Discord:** nenhum bot consegue trazer de volta um membro que
saiu do servidor. A restauração de membros só devolve cargos e apelidos de quem
ainda está lá.

---

## Hospedagem

Um bot Discord mantém uma conexão aberta o tempo todo, então ele precisa rodar
como **processo contínuo** (worker), e não como função serverless de vida curta.
Plataformas de função serverless não servem para este projeto.

O que a hospedagem precisa oferecer:

1. Processo que fica ligado sem tempo limite
2. PostgreSQL acessível
3. Disco persistente, se você quiser guardar os backups em JSON
4. As variáveis do `.env` configuradas no painel, nunca no repositório
