import streamlit as st
import firebase_admin
from firebase_admin import credentials, firestore, auth
from datetime import datetime, timedelta
import smtplib
from email.mime.text import MIMEText
import json
import google.api_core.exceptions
import google.api_core.retry as retry
import random
import pandas as pd
import time

st.markdown(
    """
    <style>
        table {
            display: block !important;
            width: fit-content !important; /* Ou tente width: -webkit-fill-available !important; */
        }
        div[data-testid="stForm"] {
            display: block !important;
        }
        .status-disponivel { background-color: forestgreen; color: white; }
        .status-ocupado { background-color: firebrick; color: white; }
        .status-indisponivel { background-color: orange; color: white; }
        .status-extra { background-color: #1E90FF; color: white; } /* Azul para Pezim */
    </style>
    """,
    unsafe_allow_html=True,
)

# Carregar as credenciais do Firebase e-mail a partir do Streamlit secrets
FIREBASE_CREDENTIALS = None
EMAIL = None
SENHA = None

try:
    # Carregar credenciais do Firebase
    firebase_credentials_json = st.secrets["firebase"]["FIREBASE_CREDENTIALS"]
    FIREBASE_CREDENTIALS = json.loads(firebase_credentials_json)

    # Carregar credenciais de e-mail
    EMAIL = st.secrets["email"]["EMAIL_CREDENCIADO"]
    SENHA = st.secrets["email"]["EMAIL_SENHA"]

except KeyError as e:
    st.error(f"Chave ausente no arquivo secrets.toml: {e}")
except json.JSONDecodeError as e:
    st.error(f"Erro ao decodificar as credenciais do Firebase: {e}")
except Exception as e:
    st.error(f"Erro inesperado: {e}")

# Inicializar Firebase com as credenciais
if FIREBASE_CREDENTIALS:
    if not firebase_admin._apps:  # Verifica se o Firebase já foi inicializado
        try:
            cred = credentials.Certificate(FIREBASE_CREDENTIALS)
            firebase_admin.initialize_app(cred)
        except Exception as e:
            st.error(f"Erro ao inicializar o Firebase: {e}")


# Obter referência do Firestore
db = firestore.client() if firebase_admin._apps else None

# Dados básicos
# A lista de horários base será gerada dinamicamente na tabela

servicos = {
    "Tradicional": 15,
    "Social": 18,
    "Degradê": 23,
    "Pezim": 7,
    "Navalhado": 25,
    "Barba": 15,
    "Abordagem de visagismo": 45,
    "Consultoria de visagismo": 65,
}

# Lista de serviços para exibição
lista_servicos = list(servicos.keys())

barbeiros = ["Lucas Borges", "Aluizio"]

# Função para enviar e-mail
def enviar_email(assunto, mensagem):
    try:
        msg = MIMEText(mensagem)
        msg['Subject'] = assunto
        msg['From'] = EMAIL
        msg['To'] = EMAIL

        with smtplib.SMTP('smtp.gmail.com', 587) as server:
            server.starttls()
            server.login(EMAIL, SENHA)  # Login usando as credenciais do e-mail
            server.sendmail(EMAIL, EMAIL, msg.as_string())
    except Exception as e:
        st.error(f"Erro ao enviar e-mail: {e}")

def salvar_agendamento(data, horario, nome, telefone, servicos, barbeiro):
    chave_agendamento = f"{data}_{horario}_{barbeiro}"
    agendamento_ref = db.collection('agendamentos').document(chave_agendamento)

    # Converter a string de data para um objeto datetime.datetime
    data_obj = datetime.strptime(data, '%d/%m/%Y')

    @firestore.transactional
    def atualizar_agendamento(transaction):
        doc = agendamento_ref.get(transaction=transaction)
        if doc.exists:
            raise ValueError("Horário já ocupado.")
        transaction.set(agendamento_ref, {
            'nome': nome,
            'telefone': telefone,
            'servicos': servicos,
            'barbeiro': barbeiro,
            'data': data_obj,  # Salvar o objeto datetime.datetime no Firestore
            'horario': horario
        })

    transaction = db.transaction()
    try:
        atualizar_agendamento(transaction)
    except ValueError as e:
        st.error(f"Erro ao salvar agendamento: {e}")
    except Exception as e:
        st.error(f"Erro inesperado ao salvar agendamento: {e}")

# Função para cancelar agendamento no Firestore
def cancelar_agendamento(data, horario, telefone, barbeiro):
    chave_agendamento = f"{data}_{horario}_{barbeiro}"
    agendamento_ref = db.collection('agendamentos').document(chave_agendamento)
    try:
        doc = agendamento_ref.get()
        # Usa .get() para evitar KeyError se 'telefone' não existir
        if doc.exists and doc.to_dict().get('telefone') == telefone:
            agendamento_data = doc.to_dict()

            # *** ADICIONADO: Guarda a lista de serviços ANTES de deletar ***
            servicos_cancelados = agendamento_data.get('servicos', [])

            # Lógica de formatação da data (mantida, mas atribui a 'data_str')
            data_firestore = agendamento_data.get('data')
            if isinstance(data_firestore, datetime):
                 agendamento_data['data_str'] = data_firestore.strftime('%d/%m/%Y') # Salva em data_str
            # ...(resto da lógica de data como estava, atribuindo a 'data_str')...
            elif isinstance(data_firestore, str):
                 # Tenta garantir formato dd/mm/yyyy, salva como está se falhar
                 try:
                     agendamento_data['data_str'] = datetime.strptime(data_firestore, '%d/%m/%Y').strftime('%d/%m/%Y')
                 except ValueError:
                      agendamento_data['data_str'] = data_firestore # Mantém original se não for dd/mm/yyyy
            else:
                 agendamento_data['data_str'] = "Data inválida" # Caso tipo inesperado

            # Deleta o documento
            agendamento_ref.delete()

            # *** ADICIONADO: Inclui a lista de serviços nos dados retornados ***
            agendamento_data['servicos_cancelados'] = servicos_cancelados

            return agendamento_data # Retorna dados incluindo serviços cancelados e data_str
        else:
            return None # Não encontrado ou telefone não confere
    except Exception as e:
        st.error(f"Erro ao acessar o Firestore para cancelar: {e}")
        return None

# Nova função para desbloquear o horário seguinte
def desbloquear_horario(data, horario, barbeiro):
    chave_bloqueio = f"{data}_{horario}_{barbeiro}_BLOQUEADO" # Modificação aqui
    agendamento_ref = db.collection('agendamentos').document(chave_bloqueio)
    try:
        doc = agendamento_ref.get()
        if doc.exists and doc.to_dict()['nome'] == "BLOQUEADO":
            print(f"Tentando excluir a chave: {chave_bloqueio}")
            agendamento_ref.delete()
            print(f"Horário {horario} do barbeiro {barbeiro} na data {data} desbloqueado.")
    except Exception as e:
        st.error(f"Erro ao desbloquear horário: {e}")

# Função para verificar disponibilidade do horário no Firebase
# --- Função verificar_disponibilidade (NOME MANTIDO, COMPORTAMENTO ALTERADO) ---
@st.cache_data(ttl=60) # Mantém o cache
def verificar_disponibilidade(data, horario, barbeiro=None):
    """
    Verifica o status de um horário.
    RETORNA:
    - None: Se estiver livre.
    - "BLOQUEADO": Se estiver explicitamente bloqueado.
    - dict: Dados do agendamento se houver um agendamento normal.
    """
    if not db:
        st.error("Firestore não inicializado.")
        return "ERRO_DB" # Retorna um status de erro

    if not barbeiro:
         # Se nenhum barbeiro for especificado, não podemos verificar um horário específico.
         # Retornar um status indicando isso ou erro? Vamos retornar um erro por enquanto.
         # Ou talvez deva iterar por todos? Melhor exigir o barbeiro para esta função.
         print("AVISO: verificar_disponibilidade chamada sem barbeiro específico.")
         return "ERRO_BARBEIRO" # Não pode verificar sem barbeiro

    chave_agendamento = f"{data}_{horario}_{barbeiro}"
    agendamento_ref = db.collection('agendamentos').document(chave_agendamento)
    chave_bloqueio = f"{data}_{horario}_{barbeiro}_BLOQUEADO"
    bloqueio_ref = db.collection('agendamentos').document(chave_bloqueio)

    try:
        # 1. Verificar bloqueio explícito
        doc_bloqueio = bloqueio_ref.get()
        if doc_bloqueio.exists and doc_bloqueio.to_dict().get('nome') == "BLOQUEADO":
            #print(f"DEBUG Check: {data} {horario} {barbeiro} -> BLOQUEADO")
            return "BLOQUEADO"

        # 2. Verificar agendamento normal
        doc_agendamento = agendamento_ref.get()
        if doc_agendamento.exists:
            #print(f"DEBUG Check: {data} {horario} {barbeiro} -> AGENDAMENTO ENCONTRADO")
            return doc_agendamento.to_dict()

        # 3. Se não encontrou bloqueio nem agendamento, está livre
        #print(f"DEBUG Check: {data} {horario} {barbeiro} -> LIVRE (None)")
        return None

    except google.api_core.exceptions.RetryError as e:
        st.error(f"Erro de conexão com o Firestore (verificar_disponibilidade): {e}")
        return "ERRO_CONEXAO"
    except Exception as e:
        st.error(f"Erro inesperado ao verificar disponibilidade: {e}")
        return "ERRO_INESPERADO"

# --- Função verificar_disponibilidade_horario_seguinte (MODIFICADA para usar nova lógica) ---
@retry.Retry()
def verificar_disponibilidade_horario_seguinte(data, horario_atual, barbeiro):
    """Verifica se o *próximo* slot está livre (retorno None da verificar_disponibilidade)."""
    if not db:
        st.error("Firestore não inicializado.")
        return False
    try:
        horario_seguinte_dt = datetime.strptime(horario_atual, '%H:%M') + timedelta(minutes=30)
        # Verifica limite do expediente
        if horario_seguinte_dt.hour >= 20:
            return False
        horario_seguinte_str = horario_seguinte_dt.strftime('%H:%M')

        # Usa a função modificada para checar o status do horário seguinte
        status_seguinte = verificar_disponibilidade(data, horario_seguinte_str, barbeiro)

        # Está disponível APENAS se o retorno for None (livre)
        return status_seguinte is None

    except ValueError:
         st.error(f"Formato de horário inválido: {horario_atual}")
         return False
    except Exception as e:
        st.error(f"Erro inesperado ao verificar horário seguinte: {e}")
        return False

# Função para bloquear horário para um barbeiro específico
def bloquear_horario(data, horario, barbeiro):
    chave_bloqueio = f"{data}_{horario}_{barbeiro}_BLOQUEADO"
    # Garante que data seja string dd/mm/yyyy
    data_str = data if isinstance(data, str) else data.strftime('%d/%m/%Y')
    try:
        data_obj_para_salvar = datetime.strptime(data_str, '%d/%m/%Y')
    except ValueError:
        st.error(f"Erro ao converter data '{data_str}' para bloqueio.")
        # Decide o que fazer em caso de erro, talvez retornar ou logar
        # Por ora, vamos salvar a string como fallback, mas idealmente deveria parar
        data_obj_para_salvar = data_str

    db.collection('agendamentos').document(chave_bloqueio).set({
        'nome': "BLOQUEADO",
        'telefone': "BLOQUEADO",
        'servicos': ["BLOQUEADO"],
        'barbeiro': barbeiro,
        # *** MUDANÇA AQUI: Salvar o objeto datetime ***
        'data': data_obj_para_salvar,
        'horario': horario
    })
    # print(f"DEBUG: Horário {horario} ... BLOQUEADO.") # Mantenha prints de debug se útil


# Interface Streamlit
st.title("Barbearia Lucas Borges - Agendamentos")
st.header("Faça seu agendamento ou cancele")
st.image("https://github.com/barbearialb/sistemalb/blob/main/icone.png?raw=true", use_container_width=True)

if 'data_agendamento' not in st.session_state:
    st.session_state.data_agendamento = datetime.today().date()  # Inicializar como objeto date

if 'date_changed' not in st.session_state:
    st.session_state['date_changed'] = False

def handle_date_change():
    st.session_state.data_agendamento = st.session_state.data_input_widget  # Atualizar com o objeto date
    verificar_disponibilidade.clear()

data_agendamento_obj = st.date_input("Data para visualizar disponibilidade", min_value=datetime.today(), key="data_input_widget", on_change=handle_date_change)
data_para_tabela = data_agendamento_obj.strftime('%d/%m/%Y')  # Formatar o objeto date

# Tabela de Disponibilidade (Renderizada com a data do session state) FORA do formulário
st.subheader("Disponibilidade dos Barbeiros")

html_table = '<table style="font-size: 14px; border-collapse: collapse; width: 100%; border: 1px solid #ddd;"><tr><th style="padding: 8px; border: 1px solid #ddd; background-color: #0e1117; color: white;">Horário</th>'
for barbeiro in barbeiros:
    html_table += f'<th style="padding: 8px; border: 1px solid #ddd; background-color: #0e1117; color: white;">{barbeiro}</th>'
html_table += '</tr>'

data_obj_tabela = data_agendamento_obj
dia_da_semana_tabela = data_obj_tabela.weekday()
horarios_tabela = [f"{h:02d}:{m:02d}" for h in range(8, 20) for m in (0, 30)]

# >>> SUBSTITUA A PARTIR DAQUI <<<
for horario in horarios_tabela:
    html_table += f'<tr><td style="padding: 8px; border: 1px solid #ddd;">{horario}</td>'
    for barbeiro in barbeiros:
        status_texto = "Erro"
        status_classe_css = "status-indisponivel" # Padrão erro

        hora_int = int(horario.split(':')[0])

        # 1. Verificar Horário de Almoço
        em_almoco = False
        if dia_da_semana_tabela < 5: # Segunda a Sexta
             if barbeiro == "Lucas Borges" and hora_int == 12: em_almoco = True
             elif barbeiro == "Aluizio" and hora_int == 13: em_almoco = True

        if em_almoco:
            status_texto = "Indisponível"
            status_classe_css = "status-indisponivel"
        else:
            # 2. Verificar Agendamento/Bloqueio usando a função modificada
            status = verificar_disponibilidade(data_para_tabela, horario, barbeiro)

            if status is None: # Livre
                status_texto = "Disponível"
                status_classe_css = "status-disponivel"
            elif status == "BLOQUEADO":
                status_texto = "Indisponível" # Ou "Bloqueado"
                status_classe_css = "status-indisponivel"
            elif isinstance(status, dict): # Agendamento existe
                servicos_no_horario = status.get('servicos', [])
                # NOVA LÓGICA: Azul apenas para UM Pezim, Vermelho para DOIS Pezins ou Outros
                if servicos_no_horario == ["Pezim"]: # Exatamente UM Pezim
                    status_texto = "Serviço extra (rápido)"
                    status_classe_css = "status-extra" # Azul
                # elif servicos_no_horario == ["Pezim", "Pezim"]: # Exatamente DOIS Pezins
                #     status_texto = "Ocupado"
                #     status_classe_css = "status-ocupado" # Vermelho
                # else: # Qualquer outra combinação (Pezim + Outro, só Outro, dois Pezins)
                #     status_texto = "Ocupado"
                #     status_classe_css = "status-ocupado" # Vermelho
                # Simplificação: Se não for exatamente um Pezim, está ocupado
                else:
                     status_texto = "Ocupado"
                     status_classe_css = "status-ocupado" # Vermelho para 2 Pezins ou outras combinações

            else: # Erro retornado pela função
                 status_texto = f"Erro ({status})"
                 status_classe_css = "status-indisponivel"


        html_table += f'<td class="{status_classe_css}" style="padding: 8px; border: 1px solid #ddd; text-align: center; height: 30px;">{status_texto}</td>'

html_table += '</tr>'
# >>> ATÉ AQUI <<<
html_table += '</table>'
st.markdown(html_table, unsafe_allow_html=True)

# Aba de Agendamento (FORMULÁRIO)
with st.form("agendar_form"):
    st.subheader("Agendar Horário")
    nome = st.text_input("Nome")
    telefone = st.text_input("Telefone")

    # Usar o valor do session state para a data dentro do formulário
    data_agendamento = st.session_state.data_agendamento.strftime('%d/%m/%Y') # Formatar para string aqui

    # Geração da lista de horários completa para agendamento
    horarios_base_agendamento = [f"{h:02d}:{m:02d}" for h in range(8, 20) for m in (0, 30)]

    barbeiro_selecionado = st.selectbox("Escolha o barbeiro", barbeiros + ["Sem preferência"])

    # Filtrar horários de almoço com base no barbeiro selecionado
    horarios_filtrados = []
    for horario in horarios_base_agendamento:
        horarios_filtrados.append(horario)

    horario_agendamento = st.selectbox("Horário", horarios_filtrados)  # Mantenha esta linha

    servicos_selecionados = st.multiselect("Serviços", lista_servicos)

    # Exibir os preços com o símbolo R$
    servicos_com_preco = {servico: f"R$ {preco}" for servico, preco in servicos.items()}
    st.write("Preços dos serviços:")
    for servico, preco in servicos_com_preco.items():
        st.write(f"{servico}: {preco}")

    submitted = st.form_submit_button("Confirmar Agendamento")

if submitted:
    # Limpa cache ANTES de qualquer verificação para ter dados frescos
    verificar_disponibilidade.clear()
    time.sleep(0.5) # Pequena pausa

    with st.spinner("Processando agendamento..."):
        # --- Flags e Variáveis de Controle ---
        erro = False
        mensagem_erro = ""
        barbeiro_final = None # Barbeiro que realizará o serviço
        # Tipos de ação: "NENHUMA", "CRIAR", "ATUALIZAR_PARA_DUPLO_PEZIM", "ATUALIZAR_COM_OUTRO"
        acao_necessaria = "NENHUMA"
        chave_para_atualizar = None
        status_horario_escolhido = None # Guarda o status do horário verificado
        precisa_bloquear_proximo = False # Flag para Corte+Barba

        # --- Obter dados do formulário (a data já está como string data_agendamento_str) ---
        # Assume que data_agendamento_str = data_para_tabela (definido antes do form)
        data_agendamento_str = data_para_tabela

        # --- 1. Validações Iniciais de Dados e Serviços ---
        if not nome or not telefone or not servicos_selecionados or not horario_agendamento:
            mensagem_erro = "Por favor, preencha Nome, Telefone, Horário e selecione ao menos 1 Serviço."
            erro = True

        servicos_visagismo = ["Abordagem de visagismo", "Consultoria de visagismo"]
        if not erro:
            tem_visagismo = any(s in servicos_selecionados for s in servicos_visagismo)
            tem_pezim = "Pezim" in servicos_selecionados

            # Verifica Visagismo apenas com Lucas
            if tem_visagismo and barbeiro_selecionado == "Aluizio":
                mensagem_erro = "Apenas Lucas Borges realiza atendimentos de visagismo."
                erro = True
            # Verifica se Visagismo e Pezim estão juntos (não permitido)
            elif tem_visagismo and tem_pezim:
                 mensagem_erro = "Não é possível agendar Visagismo e Pezim no mesmo horário."
                 erro = True

        # --- 2. Verificação Principal de Disponibilidade e Lógica Pezim ---
        if not erro: # Só continua se as validações iniciais passaram
            try:
                # Define barbeiros a verificar
                barbeiros_a_verificar = []
                if barbeiro_selecionado == "Sem preferência":
                    barbeiros_a_verificar = barbeiros
                elif barbeiro_selecionado in barbeiros:
                     barbeiros_a_verificar = [barbeiro_selecionado]
                else:
                     # Caso o valor não seja válido (não deveria acontecer com selectbox)
                     mensagem_erro = f"Barbeiro '{barbeiro_selecionado}' inválido."; erro = True

                # Loop principal para encontrar barbeiro/horário válido
                if not erro:
                     for b in barbeiros_a_verificar:
                          # 2.1 Verifica Almoço do barbeiro 'b'
                          data_obj_agendamento = datetime.strptime(data_agendamento_str, '%d/%m/%Y')
                          dia_da_semana_agendamento = data_obj_agendamento.weekday()
                          hora_agendamento_int = int(horario_agendamento.split(':')[0])
                          em_almoco_barbeiro = False
                          if dia_da_semana_agendamento < 5: # Segunda a Sexta
                               if b == "Lucas Borges" and hora_agendamento_int == 12: em_almoco_barbeiro = True
                               elif b == "Aluizio" and hora_agendamento_int == 13: em_almoco_barbeiro = True
                          if em_almoco_barbeiro:
                               status_horario_escolhido = "ALMOÇO"; continue # Marca e tenta próximo

                          # 2.2 Verifica Status do Horário (usando função modificada)
                          status_horario = verificar_disponibilidade(data_agendamento_str, horario_agendamento, b)

                          # --- Determina a Ação Necessária ---
                          if status_horario is None: # A. Livre
                               barbeiro_final = b; acao_necessaria = "CRIAR"; status_horario_escolhido = status_horario; break
                          elif isinstance(status_horario, dict): # B. Ocupado por agendamento
                               servicos_existentes = status_horario.get('servicos', [])
                               if servicos_existentes == ["Pezim"]: # B.1 Slot tem UM Pezim
                                    if servicos_selecionados == ["Pezim"]: # Tentando adicionar 2º Pezim
                                         barbeiro_final = b; acao_necessaria = "ATUALIZAR_PARA_DUPLO_PEZIM"; chave_para_atualizar = f"{data_agendamento_str}_{horario_agendamento}_{b}"; status_horario_escolhido = status_horario; break
                                    else: # Tentando adicionar outro serviço (não Pezim)
                                         servicos_permitidos_com_pezim = {"Barba", "Tradicional", "Social"}
                                         if set(servicos_selecionados).issubset(servicos_permitidos_com_pezim): # É permitido?
                                              barbeiro_final = b; acao_necessaria = "ATUALIZAR_COM_OUTRO"; chave_para_atualizar = f"{data_agendamento_str}_{horario_agendamento}_{b}"; status_horario_escolhido = status_horario; break
                                         else: # Combinação não permitida
                                              servicos_nao_permitidos = set(servicos_selecionados) - servicos_permitidos_com_pezim
                                              mensagem_erro = f"Serviço(s) '{', '.join(servicos_nao_permitidos)}' não pode(m) ser agendado(s) junto com 'Pezim'. Apenas Barba, Tradicional ou Social são permitidos."
                                              erro = True; barbeiro_final = b; status_horario_escolhido = status_horario; break # Para loop, erro definido
                               else: # B.2 Slot já ocupado (2 Pezins, Pezim+Outro, etc.)
                                    status_horario_escolhido = status_horario; continue # Tenta próximo barbeiro
                          elif status_horario == "BLOQUEADO": # C. Bloqueado
                               status_horario_escolhido = status_horario; continue # Tenta próximo barbeiro
                          elif isinstance(status_horario, str) and "ERRO" in status_horario: # D. Erro
                               mensagem_erro = f"Erro ao verificar disponibilidade ({status_horario})."; erro = True; break # Para loop, erro definido

                          # Sai do loop se um erro foi definido
                          if erro: break

            except ValueError: erro = True; mensagem_erro = "Formato de data ou hora inválido."
            except Exception as e: erro = True; mensagem_erro = f"Ocorreu um erro inesperado: {e}"; st.exception(e)

            # --- 3. Tratamento Pós-Loop de Verificação ---
            # Se não houve erro ANTES e NENHUMA ação foi encontrada no loop
            if not erro and acao_necessaria == "NENHUMA":
                 # Define a mensagem de erro apropriada baseada no último status verificado
                 if status_horario_escolhido == "ALMOÇO": mensagem_erro = f"Barbeiro(s) selecionado(s) em horário de almoço ({horario_agendamento})."
                 elif status_horario_escolhido == "BLOQUEADO": mensagem_erro = f"Horário {horario_agendamento} indisponível (bloqueado)."
                 elif isinstance(status_horario_escolhido, dict): # Ocupado
                      if status_horario_escolhido.get('servicos', []) == ["Pezim", "Pezim"]: mensagem_erro = f"Horário {horario_agendamento} já ocupado com dois serviços 'Pezim'."
                      else: mensagem_erro = f"Horário {horario_agendamento} já ocupado."
                 else: # Genérico ou Sem Preferência sem opção
                      mensagem_erro = f"Horário {horario_agendamento} indisponível para {barbeiro_selecionado}."
                 erro = True # Marca que houve um erro (indisponibilidade)

            # --- 4. Verificação de Bloqueio para Corte+Barba (Apenas se não houve erro) ---
            if not erro:
                 tem_barba = "Barba" in servicos_selecionados
                 tem_corte = any(c in servicos_selecionados for c in ["Tradicional", "Social", "Degradê", "Navalhado"])
                 # Só bloqueia se tiver Corte+Barba E NÃO for o caso de adicionar o segundo Pezim
                 if tem_barba and tem_corte and acao_necessaria != "ATUALIZAR_PARA_DUPLO_PEZIM":
                     # Verifica disponibilidade do próximo usando a função MODIFICADA
                     if not verificar_disponibilidade_horario_seguinte(data_agendamento_str, horario_agendamento, barbeiro_final):
                         horario_seguinte_str = (datetime.strptime(horario_agendamento, '%H:%M') + timedelta(minutes=30)).strftime('%H:%M')
                         mensagem_erro = f"Não é possível agendar Corte+Barba. O horário seguinte ({horario_seguinte_str}) para {barbeiro_final} não está disponível."
                         erro = True
                     else:
                         precisa_bloquear_proximo = True # Marca para bloquear depois

            # --- 5. Executar Ação no Banco de Dados (Apenas se não houve erro) ---
            if not erro:
                sucesso = False
                # Executa a ação determinada ("CRIAR", "ATUALIZAR_PARA_DUPLO_PEZIM", "ATUALIZAR_COM_OUTRO")
                if acao_necessaria == "CRIAR":
                    # Chama a função original para criar (ela retorna True/False)
                    sucesso = salvar_agendamento(data_agendamento_str, horario_agendamento, nome, telefone, servicos_selecionados, barbeiro_final)
                    if not sucesso: erro = True # Erro já é mostrado por salvar_agendamento

                elif acao_necessaria == "ATUALIZAR_PARA_DUPLO_PEZIM":
                    try: # Atualiza para ["Pezim", "Pezim"]
                        agendamento_ref = db.collection('agendamentos').document(chave_para_atualizar)
                        agendamento_ref.update({'servicos': ["Pezim", "Pezim"]})
                        sucesso = True
                    except Exception as e: st.error(f"Erro BD (duplo Pezim): {e}"); erro = True

                elif acao_necessaria == "ATUALIZAR_COM_OUTRO":
                    try: # Atualiza para ["Pezim"] + [servicos permitidos]
                        agendamento_ref = db.collection('agendamentos').document(chave_para_atualizar)
                        agendamento_ref.update({'servicos': ["Pezim"] + servicos_selecionados })
                        sucesso = True
                    except Exception as e: st.error(f"Erro BD (Pezim+Outro): {e}"); erro = True

                # --- 6. Pós-Agendamento Bem-Sucedido ---
                if sucesso:
                    # 6.1 Bloquear próximo horário, se necessário
                    if precisa_bloquear_proximo:
                         horario_seguinte = (datetime.strptime(horario_agendamento, '%H:%M') + timedelta(minutes=30)).strftime('%H:%M')
                         # Chama a função original de bloqueio
                         bloquear_horario(data_agendamento_str, horario_seguinte, barbeiro_final)
                         st.info(f"Horário das {horario_seguinte} bloqueado para {barbeiro_final}.")

                    # 6.2 Mensagem, Resumo, Email, Limpar Cache, Rerun
                    resumo = f"""Nome: {nome}; Telefone: {telefone}; Data: {data_agendamento_str}; Horário: {horario_agendamento}; Barbeiro: {barbeiro_final}; Serviços: {', '.join(servicos_selecionados)}"""
                    if "ATUALIZAR" in acao_necessaria: st.success("Agendamento atualizado com sucesso!")
                    else: st.success("Agendamento confirmado com sucesso!")
                    st.info(f"Resumo: {resumo}")
                    enviar_email("Agendamento Confirmado/Atualizado", resumo)
                    verificar_disponibilidade.clear() # Limpa o cache aqui!
                    time.sleep(5)
                    st.rerun()

        # --- 7. Exibir Erro Final (Se erro=True em qualquer etapa) ---
        if erro and mensagem_erro:
            st.error(mensagem_erro)
        elif erro: # Caso genérico de erro sem mensagem específica
             st.error("Não foi possível realizar o agendamento devido a um erro.")

# Este 'else' corresponde ao 'if not nome or not telefone...' inicial
else:
    # Mantém o erro original se os campos não foram preenchidos
    st.error("Por favor, preencha todos os campos e selecione pelo menos 1 serviço.")

# Aba de Cancelamento
with st.form("cancelar_form"): # Início do formulário
    st.subheader("Cancelar Agendamento")
    telefone_cancelar = st.text_input("Telefone para Cancelamento")

    # *** data_cancelar_obj é DEFINIDO AQUI, dentro do form ***
    data_cancelar_obj = st.date_input("Data do Agendamento", min_value=datetime.today().date())

    # Geração da lista de horários completa para cancelamento
    horarios_base_cancelamento = [f"{h:02d}:{m:02d}" for h in range(8, 20) for m in (0, 30)]
    horario_cancelar = st.selectbox("Horário do Agendamento", horarios_base_cancelamento)
    barbeiro_cancelar = st.selectbox("Barbeiro do Agendamento", barbeiros)

    # Botão de submit DENTRO do form
    submitted_cancelar = st.form_submit_button("Cancelar Agendamento")

    # *** O bloco 'if submitted_cancelar' DEVE estar indentado aqui DENTRO do 'with st.form' ***
    if submitted_cancelar:
         verificar_disponibilidade.clear() # Limpa cache antes
         time.sleep(0.5)

         with st.spinner("Processando cancelamento..."):
            try: # Adiciona try-except para segurança na conversão/uso
                # *** CONVERSÃO FEITA AQUI, dentro do if, usando a variável definida acima ***
                data_cancelar_str = data_cancelar_obj.strftime('%d/%m/%Y')

                # Agora chama a função com a string formatada
                cancelado_data = cancelar_agendamento(data_cancelar_str, horario_cancelar, telefone_cancelar, barbeiro_cancelar)

                if cancelado_data is not None:
                    # --- Sucesso no cancelamento ---
                    servicos_que_foram_cancelados = cancelado_data.get('servicos_cancelados', [])
                    resumo_cancelamento = f"""
                    Agendamento Cancelado:
                    Nome: {cancelado_data.get('nome', 'N/A')}
                    Telefone: {cancelado_data.get('telefone', 'N/A')}
                    Data: {cancelado_data.get('data_str', 'N/A')}
                    Horário: {cancelado_data.get('horario', 'N/A')}
                    Barbeiro: {cancelado_data.get('barbeiro', 'N/A')}
                    Serviços: {', '.join(servicos_que_foram_cancelados)}
                    """
                    enviar_email("Agendamento Cancelado", resumo_cancelamento)
                    st.success("Agendamento cancelado com sucesso!")
                    st.info(resumo_cancelamento)

                    # Verificar desbloqueio do horário seguinte
                    if "Barba" in servicos_que_foram_cancelados and any(c in servicos_que_foram_cancelados for c in ["Tradicional", "Social", "Degradê", "Navalhado"]):
                         try:
                             horario_ag_cancelado = cancelado_data.get('horario')
                             data_ag_cancelado_str_retornada = cancelado_data.get('data_str') # Usa data_str retornada
                             barbeiro_do_ag = cancelado_data.get('barbeiro')

                             if horario_ag_cancelado and data_ag_cancelado_str_retornada and barbeiro_do_ag and data_ag_cancelado_str_retornada != "Data inválida":
                                 horario_seguinte_dt = datetime.strptime(horario_ag_cancelado, '%H:%M') + timedelta(minutes=30)
                                 if horario_seguinte_dt.hour < 20:
                                     horario_seguinte_str = horario_seguinte_dt.strftime('%H:%M')
                                     desbloquear_horario(data_ag_cancelado_str_retornada, horario_seguinte_str, barbeiro_do_ag)
                                     st.info(f"Horário seguinte ({horario_seguinte_str}) desbloqueado.")
                             else:
                                 st.warning("Dados incompletos/inválidos retornados para desbloquear.")
                         except Exception as e:
                             st.error(f"Erro ao desbloquear horário seguinte: {e}")

                    verificar_disponibilidade.clear() # Limpa DEPOIS
                    time.sleep(5)
                    st.rerun()
                else:
                    # --- Falha no cancelamento ---
                    st.error("Não foi encontrado um agendamento correspondente aos dados informados (Data, Horário, Barbeiro e Telefone).")

            except AttributeError:
                 # Este erro ocorreria se data_cancelar_obj não fosse um objeto de data válido
                 st.error("Erro Crítico: Não foi possível obter ou formatar a data selecionada para cancelamento. Verifique a indentação e a definição de 'data_cancelar_obj'.")
            except Exception as e:
                 st.error(f"Erro inesperado durante o processamento do cancelamento: {e}")
                 st.exception(e) # Mostra detalhes do erro no log/terminal
