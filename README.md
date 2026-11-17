# Assistente da Barbearia Imperial

Protótipo acadêmico de atendimento com agenda, API, interface web e busca RAG integrada ao GPT. O backend valida horários e impede conflitos antes de gravar. A interface e o serviço rodam em Windows, macOS e Linux com Python 3.11 ou 3.12; também há Docker para execução multiplataforma.

## Atualização de 17 de novembro de 2026

- **Atendimento automatizado** com contexto, consultas ao catálogo e à agenda, até três sugestões reais e alternativas de dia ou profissional. As regras para horários numéricos funcionam no site e nas mensagens livres do Telegram, com esclarecimento de pedidos ambíguos ou negados.
- **Sugestões clicáveis no site** que preenchem serviço, profissional, data e horário em **Agendar**. O sistema consulta novamente as vagas e o catálogo e exige a confirmação final da reserva.
- **Agenda mais visual** com horários agrupados, seleção de profissional após a hora em **Sem preferência**, feriados de Ribeirão Preto em amarelo e interface simplificada no calendário e no chat.
- **Atendimento humano** com formulário separado da conversa, protocolo criado após a confirmação do cliente no site, acompanhamento da situação e fila privada para a equipe assumir e resolver. O retorno da equipe é pelo telefone cadastrado.
- **Confiabilidade e privacidade** com confirmação idempotente, preservação do contato ao trocar de data, botões do Telegram vinculados às vagas exatas, recuperação de comprovantes e proteção de códigos privados no histórico enviado ao modelo.
- **Avaliação do TCC** com telemetria de interações e reservas, exportação pseudonimizada, métricas de latência e encaminhamento e roteiro de aceitação e avaliação com participantes.

As validações locais usam testes automatizados e ensaios no navegador com banco de avaliação separado. O teste real do Telegram depende de `TELEGRAM_BOT_TOKEN`, e o uso da IA depende de `OPENAI_API_KEY`. A coleta de resultados com participantes está descrita no [roteiro de avaliação](eval/roteiro_automacao.md).

## Dados usados

Os parâmetros iniciais vieram de `ParametrosBarbearia.docx`. A Barbearia Imperial funciona de segunda a sábado, das 08:00 às 12:00 e das 13:30 às 20:00. Na escala padrão, Rafael folga na segunda, Diego na quarta e Alecio no sábado. A base de conhecimento tem 15 documentos curtos. O telefone e endereço foram fornecidos como dados de demonstração.

Por padrão, clientes podem agendar e remarcar online de 1 a 7 dias de antecedência. O calendário informa o período aberto. Em instalações existentes com `.env` próprio, defina `MAX_BOOKING_DAYS=7` e reinicie a API e a interface para aplicar a nova janela.

Em **Meus horários**, informe o telefone e o código privado do comprovante para consultar, remarcar ou cancelar uma reserva. Cada código dá acesso a uma única reserva. Use **Agendar** para criar reservas. O chat ajuda com informações e disponibilidade.

O atendimento guiado consulta serviços, preços e vagas mesmo sem chave de IA. Experimente «Quero agendar corte», seguido de «amanhã à tarde». Ele aproveita o contexto, apresenta até três opções reais e procura alternativas quando a combinação estiver indisponível. Perguntas abertas e políticas com recuperação RAG usam a IA quando configurada. Se o serviço de IA falhar, o atendimento oferece consultas guiadas e encaminhamento à equipe.

No site, as opções retornadas pelo chat são clicáveis e abrem **Agendar** com serviço, profissional, data e horário preenchidos. A disponibilidade e o catálogo são consultados novamente ao escolher. A reserva exige revisão e confirmação explícita.

Pedidos numéricos como «depois das 15h», «a partir das 15h», «antes das 15h», «entre 10h e 12h» e «às 15h30» funcionam sem IA, assim como «o último horário de sábado». Intervalos e limites de término consideram a duração do serviço. Expressões ambíguas pedem esclarecimento.

Em **Conversar** ou no painel flutuante, use **Falar com a equipe**. A tela de contato é aberta primeiro. O protocolo só é criado ao preencher nome e telefone e confirmar em **Solicitar atendimento**. O telefone desse formulário fica disponível à equipe autorizada e é coletado fora da conversa enviada ao modelo. A equipe fará o retorno pelo telefone informado. A conversa do site continua com o assistente virtual.

O agendamento mostra um resumo da escolha e os feriados de Ribeirão Preto em amarelo. A disponibilidade em feriados segue a escala e os bloqueios cadastrados. O contato preenchido permanece ao trocar de dia. Se a resposta da confirmação se perder, **Tentar confirmar novamente** recupera a mesma reserva por uma chave de confirmação. O comprovante recuperado contém um novo código privado, que substitui o anterior.

O [plano de automação de atendimento](docs/plano-automacao.md) descreve as próximas entregas para o chat do site, a área privada da equipe e o WhatsApp.

Quando o cliente escolhe **Sem preferência**, cada horário aparece uma única vez. Após selecionar a hora, ele escolhe entre os profissionais disponíveis. Se houver apenas um, a seleção é automática e o resumo informa quem atenderá.

Na conversa do site, a linha do protocolo mostra **Aguardando a equipe**, **Em atendimento** ou **Resolvido** e atualiza a cada dez segundos enquanto aberto. O botão **Atualizar** permite consultar imediatamente. No Telegram, use `/atendimento` para consultar o protocolo mais recente, inclusive após sua resolução. A consulta fica vinculada à conversa de origem.

## Executar localmente

Use Python 3.11 ou 3.12. A opção recomendada é `uv`, que usa o arquivo `uv.lock` para instalar as mesmas versões em Windows, macOS e Linux e pode baixar uma versão compatível do Python quando necessário:

```bash
python -m pip install uv
uv sync --python 3.12
```

Se preferirem usar o `venv` e o `pip`, criem o ambiente com Python 3.12, ativem `.venv` e executem `pip install .`.

Copie `.env.example` para `.env` (`Copy-Item .env.example .env` no PowerShell ou `cp .env.example .env` no macOS/Linux). O protótipo de agenda e consultas funciona sem chave. Para ativar GPT e busca RAG, crie uma chave na plataforma da OpenAI e insira-a localmente em `OPENAI_API_KEY` no `.env`. A chave não deve ser enviada ao GitHub. Depois, rode:

```bash
uv run uvicorn barber_bot.api:app --app-dir src --reload
```

Em outro terminal, com o mesmo ambiente virtual:

```bash
uv run streamlit run src/streamlit_app.py
```

Abra o endereço local exibido pelo Streamlit. A API fica em `http://127.0.0.1:8000/docs`.

### Servidor temporário no Windows para demonstração

O Windows deste computador já tem o cliente SSH. Dê duplo clique em `IniciarDemoWindows.bat` para abrir a API, a interface, o bot Telegram e um túnel Pinggy em janelas separadas. O túnel encaminha a porta 8501 em `127.0.0.1` e mostra uma URL HTTPS pública na janela **Link publico Pinggy**. Se quiser abrir apenas o túnel após iniciar os serviços, use `AbrirLinkPublico.bat`. A confirmação da chave SSH é salva em `ssh_known_hosts` na pasta do projeto; se o SSH pedir uma senha, pressione Enter.

Não precisa instalar outro programa, criar uma conta, ter domínio ou alterar o roteador. A URL é temporária e muda quando o túnel reinicia; qualquer pessoa que receba o link poderá abrir o site. Para acessar a área da equipe, acrescente `/equipe` ao endereço público e entre com uma conta cadastrada. Mantenha as janelas abertas e impeça o computador de suspender durante a demonstração. Feche as janelas ao terminar.

## Agenda da equipe

Compartilhe `http://127.0.0.1:8501/equipe` com a equipe na instalação local. A página pública de reservas não exibe o atalho. O endereço da equipe mostra apenas a tela de login até validar uma conta cadastrada. A API confere a sessão e as permissões a cada operação interna.

- O dono pode consultar e gerenciar a agenda de todos os profissionais. Cada barbeiro gerencia apenas sua própria agenda.
- Em **Atendimento**, a equipe encontra os protocolos do site e do Telegram. Um barbeiro pode assumir um chamado livre e gerenciar os seus, enquanto o dono acompanha todos. Ao assumir, o responsável acessa o contato, registra observações e move o chamado para **Resolvido**. A API impede dois profissionais de assumir o mesmo chamado e preserva os chamados de instalações anteriores.
- **Agenda** abre no dia atual e oferece atalhos para hoje e amanhã, além de filtros por data e situação. O dono também pode filtrar por profissional. Em **Balcão**, a equipe pode registrar reservas feitas presencialmente ou por telefone, de hoje até 30 dias à frente.
- Entregue o código privado ao cliente ao confirmar uma reserva no balcão. Para reservas anteriores à mudança ou em caso de perda, abra a reserva na agenda e emita outro código após verificar a identidade do cliente. A emissão invalida o código anterior.
- Em **Bloqueios**, a equipe pode reservar intervalos ou dias inteiros para folgas e férias. O sistema informa os códigos das reservas confirmadas que precisam ser resolvidas antes de bloquear o período.
- **Serviços** e **Informações e escala** ficam disponíveis ao dono. As alterações de preço e duração valem para novas reservas. Mudanças de expediente ou escala que deixariam uma reserva futura sem atendimento são recusadas.

Em uma instalação nova, crie as contas da equipe antes de tentar entrar. Execute os comandos no mesmo diretório e com o mesmo `DATABASE_PATH` usado pela API. O comando pede a senha de forma interativa e aceita de 12 a 128 caracteres.

```bash
uv run python -m barber_bot.staff dono
uv run python -m barber_bot.staff rafael --barber Rafael
uv run python -m barber_bot.staff diego --barber Diego
uv run python -m barber_bot.staff alecio --barber Alecio
```

Se alguém perder a senha, repita o comando da conta com `--replace`. Preserve `--barber` para manter a conta vinculada ao barbeiro. A redefinição encerra as sessões anteriores dessa conta e exige um novo login.

```bash
uv run python -m barber_bot.staff rafael --barber Rafael --replace
```

O banco SQLite e o arquivo privado de acessos ficam na instalação local. Após criar ou redefinir uma conta, valide o acesso em `/equipe`.

Em um banco já existente, as escalas de trabalho gravadas são preservadas. Para adotar a nova escala padrão, entre como dono e abra **Informações e escala**. Defina a folga de Rafael na segunda, Diego na quarta e Alecio no sábado. A agenda bloqueia domingos, e o painel avisa se houver reservas futuras que impeçam a alteração de escala.

## Bot do Telegram

O bot compartilha o mesmo assistente, catálogo, regras de disponibilidade e banco SQLite do site. Em uma conversa privada, use `/start` para ver as opções, `/horarios` para consultar vagas ou `/agendar` para reservar. A reserva só é gravada depois que o cliente revisa e confirma o resumo. Ela aparece na agenda do painel da equipe e o bot envia o código privado da reserva ao cliente. Não encaminhe esse código a outras pessoas.

Crie um bot com `@BotFather` no Telegram e copie o token apenas para o `.env` local:

```text
TELEGRAM_BOT_TOKEN=token_fornecido_pelo_BotFather
```

Não coloque o token no GitHub nem o envie em mensagens. Para dúvidas com IA, configure também `OPENAI_API_KEY`, habilite créditos de API e crie o índice RAG conforme a seção **Criar índice da base de conhecimento**. Agendamento e consulta guiada de disponibilidade funcionam sem a chave da OpenAI.

Com a API e o Streamlit já iniciados em dois terminais, inicie o bot em um terceiro, na pasta do projeto:

```bash
uv run python -m barber_bot.telegram_bot
```

O bot usa long polling (`getUpdates`), portanto não precisa de endereço público, webhook ou porta aberta no roteador para receber mensagens. Deixe esse terminal e o computador ligados enquanto o bot estiver em uso. Para encerrar, pressione `Ctrl+C`. O histórico temporário da conversa expira após 30 minutos; reservas confirmadas permanecem no banco e no painel.

O acesso por função e a separação entre a agenda individual e a da equipe seguem o modelo documentado pelo [Square Appointments](https://squareup.com/help/us/en/article/5350-create-staff-member-profiles-for-square-appointments) e pelo [Fresha](https://www.fresha.com/help-center/knowledge-base/team/100687-create-permission-roles). A validação no servidor segue a [orientação de autorização da OWASP](https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html).

No Telegram, `/atendente` inicia a coleta de nome e telefone para retorno da equipe, fora da conversa com IA. `/comprovante` recupera o comprovante mais recente ligado à conversa privada e renova o código de acesso. `/cancelar` interrompe a conversa de agendamento. Para cancelar uma reserva confirmada, use **Meus horários** no site.

## Criar índice da base de conhecimento

Depois de configurar a chave GPT, rode na pasta do projeto:

```bash
python -c "from barber_bot.knowledge import ingest_knowledge; print(ingest_knowledge())"
```

O índice local usa Chroma e trechos de até 700 tokens com sobreposição de 100. A busca gera embeddings pela API da OpenAI.

## Docker

Com Docker instalado e `.env` configurado, inicie a aplicação:

```bash
docker compose up --build
```

A interface abre em `http://localhost:8501` e a documentação da API em `http://localhost:8000/docs`. A pasta `data/` fica montada para preservar o banco e o índice entre reinicializações.

## Testes e avaliação

O [roteiro de aceitação da automação](eval/roteiro_automacao.md) descreve como demonstrar reserva confirmada, alternativas sem vagas e atendimento humano, além da avaliação com os baselines existentes. A exportação usa sessões pseudonimizadas e omite mensagens e contatos.

```bash
uv run python eval/export_logs.py --out eval/telemetria.csv
uv run python eval/metrics.py --latency eval/telemetria.csv --operations eval/telemetria.csv
```

Os eventos de reserva são deduplicados e separados das interações para calcular latência. FCR depende dos rótulos preenchidos pelos avaliadores.

```bash
uv run pytest
```

Os testes cobrem conflito de agenda, confirmação explícita, duração fora do expediente, agendamento inexistente e escala semanal. A pasta `eval/` inclui 150 frases rotuladas em dez intenções, 30 cenários FCR, um baseline por palavras-chave, um baseline GPT sem RAG e os cálculos de acurácia, F1 macro, FCR, latência média e Likert.

Com a chave GPT configurada, rode o classificador no conjunto de intenções e calcule as métricas:

```bash
uv run python eval/run_intent_eval.py
uv run python eval/export_logs.py --out eval/latencias.csv
uv run python eval/metrics.py --intents eval/resultados_intencao.csv --fcr eval/cenarios_fcr.csv --latency eval/latencias.csv --likert eval/respostas_formulario.csv
```

`run_intent_eval.py` faz chamadas ao GPT para cada frase e pode gerar custo de API. O script `export_logs.py` cria o CSV de latências e horários; o relatório calcula latência média por resposta e duração média da sessão. Para Likert, use o questionário em `eval/questionario_likert.md`, exporte respostas sem e-mail e nomeie a coluna do item escolhido como `rating`. Nos cenários FCR, dois avaliadores registram suas avaliações nos campos individuais e depois preenchem os campos de consenso usados no cálculo. O projeto não fabrica dados de voluntários.

O baseline por palavras-chave está em `eval/baselines.py::keyword_faq`; o baseline GPT sem recuperação está em `eval/baselines.py::llm_without_rag`; o protótipo com RAG e ferramentas é acessado pela rota `/chat` da API. Usem as mesmas perguntas e as mesmas regras de avaliação para os três modos.

## Estrutura

```text
data/knowledge/     documentos da base RAG
src/barber_bot/     banco, regras, busca, agente e API
src/streamlit_app.py interface web
tests/              testes da lógica de agenda
eval/               dataset, baselines e métricas
```

## Configuração antes da banca

- Confirmar se preço e duração dos serviços continuam válidos.
- Definir e substituir telefone/endereço de demonstração, se necessário.
- Confirmar se a multa de 30% por falta pode ser aplicada e como comunicá-la.
- A chave de API e o uso de embeddings podem gerar custos na conta OpenAI.
- Consultar a orientadora sobre consentimento e eventual aprovação ética antes de recrutar voluntários.
- Fazer deploy público somente após revisar privacidade, autenticação, rate limits e segurança do banco. A configuração atual é uma demo acadêmica local.
