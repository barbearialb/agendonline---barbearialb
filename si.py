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
import pandas as pd  # Importar a biblioteca pandas
import time  # Importar a biblioteca time

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
    </style>
    """,
    unsafe_allow_html=True,
)

# Carregar as credenciais do Firebase e e-mail a partir do Streamlit secrets
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
servicos = {
    "Tradicional": 15,
    "Social": 18,
    "Degradê": 23,
    "Navalhado": 25,
    "Pezim": 7,
    "Barba": 15,
}

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
        if doc.exists and doc.to_dict()['telefone'] == telefone:
            agendamento_data = doc.to_dict()
            # Verificar se a data é um objeto datetime antes de formatar
            if isinstance(agendamento_data['data'], datetime):
                agendamento_data['data'] = agendamento_data['data'].date().strftime('%d/%m/%Y')
            elif isinstance(agendamento_data['data'], str):
                # Se for string, tentamos converter para datetime
                try:
                    # Tentar converter de diferentes formatos comuns
                    try:
                        agendamento_data['data'] = datetime.strptime(agendamento_data['data'], '%Y-%m-%d').date().strftime('%d/%m/%Y')
                    except ValueError:
                        agendamento_data['data'] = datetime.strptime(agendamento_data['data'], '%d/%m/%Y').date().strftime('%d/%m/%Y')

                except ValueError:
                    st.error("Formato de data inválido no Firestore")
                    return None
            else:
                st.error("Formato de data inválido no Firestore")
                return None

            agendamento_ref.delete()
            return agendamento_data
        else:
            return None
    except Exception as e:
        st.error(f"Erro ao acessar o Firestore: {e}")
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

# Função para verificar disponibilidade do horário no Firebase (MODIFICADA novamente)
@st.cache_data
def verificar_disponibilidade(data, horario, barbeiro=None):
    if not db:
        st.error("Firestore não inicializado.")
        return False

    data_obj = datetime.strptime(data, '%d/%m/%Y')
    dia_da_semana = data_obj.weekday()  # 0 = segunda, 4 = sexta

    hora = int(horario.split(':')[0])
    minuto = int(horario.split(':')[1])

    # Verificar disponibilidade com base no dia da semana e horário
    if dia_da_semana < 5:  # Segunda a sexta
        if (hora == 11 and minuto in [0, 30]):
            if barbeiro == "Aluizio":
                return -1  # Indisponível para Aluizio das 11h às 12h
        elif hora == 12:
            return -1  # Indisponível para ambos das 12h às 13h
        elif hora == 13 and minuto in [0, 30]:
            if barbeiro == "Lucas Borges":
                return -1  # Indisponível para Lucas das 13h às 14h

    # Verificar agendamento regular
    chave_agendamento = f"{data}_{horario}_{barbeiro}"
    agendamento_ref = db.collection('agendamentos').document(chave_agendamento)

    # Verificar horário bloqueado
    chave_bloqueio = f"{data}_{horario}_{barbeiro}_BLOQUEADO"
    bloqueio_ref = db.collection('agendamentos').document(chave_bloqueio)

    try:
        doc_agendamento = agendamento_ref.get()
        doc_bloqueio = bloqueio_ref.get()
        if doc_agendamento.exists or doc_bloqueio.exists:
            return False  # Ocupado
        else:
            return True  # Disponível (se as regras de horário permitirem)
    except google.api_core.exceptions.RetryError as e:
        st.error(f"Erro de conexão com o Firestore: {e}")
        return False
    except Exception as e:
        st.error(f"Erro inesperado ao verificar disponibilidade: {e}")
        return False

# Função para verificar disponibilidade do horário e do horário seguinte
@retry.Retry()
def verificar_disponibilidade_horario_seguinte(data, horario, barbeiro):
    if not db:
        st.error("Firestore não inicializado.")
        return False
    horario_seguinte = (datetime.strptime(horario, '%H:%M') + timedelta(minutes=30)).strftime('%H:%M')
    chave_agendamento_seguinte = f"{data}_{horario_seguinte}_{barbeiro}"
    agendamento_ref_seguinte = db.collection('agendamentos').document(chave_agendamento_seguinte)
    try:
        doc_seguinte = agendamento_ref_seguinte.get()
        return not doc_seguinte.exists
    except google.api_core.exceptions.RetryError as e:
        st.error(f"Erro de conexão com o Firestore: {e}")
        return False
    except Exception as e:
        st.error(f"Erro inesperado ao verificar disponibilidade: {e}")
        return False

# Função para bloquear horário para um barbeiro específico
def bloquear_horario(data, horario, barbeiro):
    chave_bloqueio = f"{data}_{horario}_{barbeiro}_BLOQUEADO"
    db.collection('agendamentos').document(chave_bloqueio).set({
        'nome': "BLOQUEADO",
        'telefone': "BLOQUEADO",
        'servicos': ["BLOQUEADO"],
        'barbeiro': barbeiro,
        'data': data,
        'horario': horario
    })


# Interface Streamlit
st.title("Barbearia Lucas Borges - Agendamentos")
st.header("Faça seu agendamento ou cancele")
st.image("https://github.com/barbearialb/sistemalb/blob/main/icone.png?raw=true", use_container_width=True)

if 'data_agendamento' not in st.session_state:
    st.session_state.data_agendamento = datetime.today().strftime('%d/%m/%Y')

if 'date_changed' not in st.session_state:
    st.session_state['date_changed'] = False

def handle_date_change():
    verificar_disponibilidade.clear()

data_agendamento_obj = st.date_input("Data para visualizar disponibilidade", min_value=datetime.today(), key="data_input_widget", on_change=handle_date_change)
data_para_tabela = st.session_state.get("data_input_widget", datetime.today()).strftime('%d/%m/%Y')

# Tabela de Disponibilidade (Renderizada com a data do session state) FORA do formulário
st.subheader("Disponibilidade dos Barbeiros")

html_table = '<table style="font-size: 14px; border-collapse: collapse; width: 100%; border: 1px solid #ddd;"><tr><th style="padding: 8px; border: 1px solid #ddd; background-color: #0e1117; color: white;">Horário</th>'
for barbeiro in barbeiros:
    html_table += f'<th style="padding: 8px; border: 1px solid #ddd; background-color: #0e1117; color: white;">{barbeiro}</th>'
html_table += '</tr>'

# Gerar horários base dinamicamente para a tabela (MOSTRAR TODOS OS HORÁRIOS)
horarios_tabela = []
for h in range(8, 20):
    for m in (0, 30):
        horarios_tabela.append(f"{h:02d}:{m:02d}")

for horario in horarios_tabela:
    html_table += f'<tr><td style="padding: 8px; border: 1px solid #ddd;">{horario}</td>'
    for barbeiro in barbeiros:
        disponibilidade_status = verificar_disponibilidade(data_para_tabela, horario, barbeiro)
        if disponibilidade_status is True:
            status = "Disponível"
            bg_color = "forestgreen"
        elif disponibilidade_status is False:
            status = "Ocupado"
            bg_color = "firebrick"  # Ou a cor que você definiu para ocupado
        elif disponibilidade_status == -1:
            status = "Indisponível"
            bg_color = "gold"
        else:
            status = ""  # Caso haja algum outro retorno inesperado
            bg_color = ""

        color_text = "white"
        html_table += f'<td style="padding: 8px; border: 1px solid #ddd; background-color: {bg_color}; text-align: center; color: {color_text}; height: 30px;">{status}</td>'
html_table += '</tr>'

html_table += '</table>'
st.markdown(html_table, unsafe_allow_html=True)

# Aba de Agendamento (FORMULÁRIO)
with st.form("agendar_form"):
    st.subheader("Agendar Horário")
    nome = st.text_input("Nome")
    telefone = st.text_input("Telefone")

    # Usar o valor do session state para a data dentro do formulário
    data_agendamento = st.session_state.data_agendamento
    data_obj_agendamento = datetime.strptime(data_agendamento, '%d/%m/%Y')
    dia_da_semana_agendamento = data_obj_agendamento.weekday()

    horarios_base_agendamento = []
    for h in range(8, 20):
        for m in (0, 30):
            horario_str = f"{h:02d}:{m:02d}"
            if dia_da_semana_agendamento == 5:  # Sábado
                horarios_base_agendamento.append(horario_str)
            elif h < 12 or h >= 14:  # Outros dias, bloquear almoço (faixa geral para agendamento)
                horarios_base_agendamento.append(horario_str)

    barbeiro_selecionado = st.selectbox("Escolha o barbeiro", barbeiros + ["Sem preferência"])
    horario_agendamento = st.selectbox("Horário", horarios_base_agendamento)

    servicos_selecionados = st.multiselect("Serviços", list(servicos.keys()))

    # Exibir os preços com o símbolo R$
    servicos_com_preco = {servico: f"R$ {preco}" for servico, preco in servicos.items()}
    st.write("Preços dos serviços:")
    for servico, preco in servicos_com_preco.items():
        st.write(f"{servico}: {preco}")

    submitted = st.form_submit_button("Confirmar Agendamento")

if submitted:
    with st.spinner("Processando agendamento..."):
        if nome and telefone and servicos_selecionados:
            data_obj_agendamento = datetime.strptime(data_agendamento, '%d/%m/%Y')
            dia_da_semana_agendamento = data_obj_agendamento.weekday()
            hora_agendamento = int(horario_agendamento.split(':')[0])
            minuto_agendamento = int(horario_agendamento.split(':')[1])

            barbeiro_valido = True
            if dia_da_semana_agendamento < 5:  # Segunda a sexta
                if (hora_agendamento == 11 and minuto_agendamento in [0, 30]):
                    if barbeiro_selecionado == "Aluizio":
                        barbeiro_valido = False
                elif hora_agendamento == 12:
                    barbeiro_valido = False
                elif hora_agendamento == 13 and minuto_agendamento in [0, 30]:
                    if barbeiro_selecionado == "Lucas Borges":
                        barbeiro_valido = False

            if not barbeiro_valido and barbeiro_selecionado != "Sem preferência":
                st.error(f"O barbeiro {barbeiro_selecionado} não está disponível neste horário. Por favor, selecione outro horário ou barbeiro.")
            else:
                barbeiro_para_agendar = barbeiro_selecionado
                if "Sem preferência" in barbeiro_selecionado:
                    barbeiros_disponiveis_agora = []
                    for b in barbeiros:
                        data_check = data_agendamento
                        horario_check = horario_agendamento
                        data_obj_check = datetime.strptime(data_check, '%d/%m/%Y')
                        dia_semana_check = data_obj_check.weekday()
                        hora_check = int(horario_check.split(':')[0])
                        minuto_check = int(horario_check.split(':')[1])

                        disponivel_regra = True
                        if dia_semana_check < 5:
                            if (hora_check == 11 and minuto_check in [0, 30]) and b == "Aluizio":
                                disponivel_regra = False
                            elif hora_check == 12:
                                disponivel_regra = False
                            elif hora_check == 13 and minuto_check in [0, 30] and b == "Lucas Borges":
                                disponivel_regra = False

                        if disponivel_regra and verificar_disponibilidade(data_check, horario_check, b) is True: # Verifica se é True agora
                            barbeiros_disponiveis_agora.append(b)

                    if barbeiros_disponiveis_agora:
                        barbeiro_para_agendar = random.choice(barbeiros_disponiveis_agora)
                    else:
                        st.error("Nenhum barbeiro disponível neste horário. Por favor, selecione outro horário.")
                        barbeiro_para_agendar = None

                if barbeiro_para_agendar:
                    disponibilidade_final = verificar_disponibilidade(data_agendamento, horario_agendamento, barbeiro_para_agendar)
                    if disponibilidade_final is True:
                        if "Barba" in servicos_selecionados and any(corte in servicos_selecionados for corte in ["Tradicional", "Social", "Degradê", "Navalhado"]):
                            if verificar_disponibilidade_horario_seguinte(data_agendamento, horario_agendamento, barbeiro_para_agendar):
                                resumo = f"""
                                Nome: {nome}
                                Telefone: {telefone}
                                Data: {data_agendamento}
                                Horário: {horario_agendamento}
                                Barbeiro: {barbeiro_para_agendar}
                                Serviços: {', '.join(servicos_selecionados)}
                                """
                                salvar_agendamento(data_agendamento, horario_agendamento, nome, telefone, servicos_selecionados, barbeiro_para_agendar)
                                # Bloquear o horário seguinte para o barbeiro selecionado
                                horario_seguinte = (datetime.strptime(horario_agendamento, '%H:%M') + timedelta(minutes=30)).strftime('%H:%M')
                                chave_bloqueio = f"{data_agendamento}_{horario_seguinte}_{barbeiro_para_agendar}_BLOQUEADO" # Modificação aqui
                                db.collection('agendamentos').document(chave_bloqueio).set({
                                    'nome': "BLOQUEADO",
                                    'telefone': "BLOQUEADO",
                                    'servicos': ["BLOQUEADO"],
                                    'barbeiro': barbeiro_para_agendar,
                                    'data': data_agendamento,
                                    'horario': horario_seguinte
                                })
                                enviar_email("Agendamento Confirmado", resumo)
                                verificar_disponibilidade.clear()
                                st.success("Agendamento confirmado com sucesso! Horário seguinte bloqueado.")
                                st.info("Resumo do agendamento:\n" + resumo)
                                st.info(f"O horário das {horario_seguinte} de {barbeiro_para_agendar} foi bloqueado.")
                                time.sleep(5)
                                st.rerun()
                            else:
                                horario_seguinte = (datetime.strptime(horario_agendamento, '%H:%M') + timedelta(minutes=30)).strftime('%H:%M')
                                st.error(f"O barbeiro {barbeiro_para_agendar} não poderá atender para corte e barba pois no horário seguinte ({horario_seguinte}) ele já está ocupado. Por favor, verifique outro barbeiro ou horário.")
                        else:
                            resumo = f"""
                            Nome: {nome}
                            Telefone: {telefone}
                            Data: {data_agendamento}
                            Horário: {horario_agendamento}
                            Barbeiro: {barbeiro_para_agendar}
                            Serviços: {', '.join(servicos_selecionados)}
                            """
                            salvar_agendamento(data_agendamento, horario_agendamento, nome, telefone, servicos_selecionados, barbeiro_para_agendar)
                            enviar_email("Agendamento Confirmado", resumo)
                            verificar_disponibilidade.clear()
                            st.success("Agendamento confirmado com sucesso!")
                            st.info("Resumo do agendamento:\n" + resumo)
                            time.sleep(5)
                            st.rerun()
                    elif disponibilidade_final is False:
                        st.error("O horário escolhido já está ocupado. Por favor, selecione outro horário ou veja outro barbeiro.")
                    elif disponibilidade_final == -1:
                        st.error(f"O barbeiro {barbeiro_para_agendar} não está disponível neste horário. Por favor, selecione outro horário ou barbeiro.")

        else:
            st.error("Por favor, preencha todos os campos e selecione pelo menos 1 serviço.")

# Aba de Cancelamento
with st.form("cancelar_form"):
    st.subheader("Cancelar Agendamento")
    telefone_cancelar = st.text_input("Telefone para Cancelamento")
    data_cancelar = st.date_input("Data do Agendamento", min_value=datetime.today()).strftime('%d/%m/%Y')
    data_obj_cancelar = datetime.strptime(data_cancelar, '%d/%m/%Y')
    dia_da_semana_cancelar = data_obj_cancelar.weekday()

    horarios_cancelar = []
    for h in range(8, 20):
        for m in (0, 30):
            horario_str = f"{h:02d}:{m:02d}"
            if dia_da_semana_cancelar == 5:
                horarios_cancelar.append(horario_str)
            elif h < 12 or h >= 14:
                horarios_cancelar.append(horario_str)

    horario_cancelar = st.selectbox("Horário do Agendamento", horarios_cancelar)
    barbeiro_cancelar = st.selectbox("Barbeiro do Agendamento", barbeiros) # Adicionando a seleção do barbeiro
    submitted_cancelar = st.form_submit_button("Cancelar Agendamento")
    if submitted_cancelar:
        with st.spinner("Processando cancelamento..."):
            cancelado = cancelar_agendamento(data_cancelar, horario_cancelar, telefone_cancelar, barbeiro_cancelar)

        if cancelado:
            resumo_cancelamento = f"""
            Nome: {cancelado['nome']}
            Telefone: {cancelado['telefone']}
            Data: {cancelado['data']}
            Horário: {cancelado['horario']}
            Barbeiro: {cancelado['barbeiro']}
            Serviços: {', '.join(cancelado['servicos'])}
            """
            enviar_email("Agendamento Cancelado", resumo_cancelamento)
            verificar_disponibilidade.clear()
            st.success("Agendamento cancelado com sucesso!")
            st.info("Resumo do cancelamento:\n" + resumo_cancelamento)
            # Verificar se o horário seguinte estava bloqueado e desbloqueá-lo
            if "Barba" in cancelado['servicos'] and any(corte in cancelado['servicos'] for corte in ["Tradicional", "Social", "Degradê", "Navalhado"]):
                horario_seguinte = (datetime.strptime(cancelado['horario'], '%H:%M') + timedelta(minutes=30)).strftime('%H:%M')
                # Adicione estas linhas temporariamente para verificar os valores
                desbloquear_horario(cancelado['data'], horario_seguinte, cancelado['barbeiro'])
                st.info("O horário seguinte foi desbloqueado.")
            time.sleep(5)
            st.rerun()
        else:
            st.error(f"Não há agendamento para o telefone informado nesse horário e com o barbeiro selecionado.")
