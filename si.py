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
horarios = []
for h in range(8, 20):
    for m in (0, 30):
        if h < 12 or h >= 14:  # Bloquear horários de almoço
            horarios.append(f"{h:02d}:{m:02d}")

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
            'data': data,
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
# Função para cancelar agendamento no Firestore
def cancelar_agendamento(data, horario, telefone):
    chave_agendamento = f"{data}_{horario}" # Remove o barbeiro da chave
    agendamento_ref = db.collection('agendamentos').document(chave_agendamento)
    try:
        doc = agendamento_ref.get()
        if doc.exists and doc.to_dict()['telefone'] == telefone:
            agendamento_ref.delete()
            return doc.to_dict()  # Retorna os dados do agendamento cancelado
        else:
            return None
    except Exception as e:
        st.error(f"Erro ao acessar o Firestore: {e}")
        return None

# Função para verificar disponibilidade do horário no Firebase
def verificar_disponibilidade(data, horario, barbeiro=None):
    if not db:
        st.error("Firestore não inicializado.")
        return False
    chave_agendamento = f"{data}_{horario}_{barbeiro}" if barbeiro else f"{data}_{horario}"
    agendamento_ref = db.collection('agendamentos').document(chave_agendamento)
    try:
        doc = agendamento_ref.get()
        return not doc.exists
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

# Aba de Agendamento
st.subheader("Agendar Horário")
nome = st.text_input("Nome")
telefone = st.text_input("Telefone")
data = st.date_input("Data", min_value=datetime.today()).strftime('%d/%m/%Y')
dia_da_semana = datetime.strptime(data, '%d/%m/%Y').weekday()
if dia_da_semana < 5:  # Segunda a sexta-feira
    horarios = []
    for h in range(8, 20):
        for m in (0, 30):
            if h < 12 or h >= 14:  # Bloquear horários de almoço
                horarios.append(f"{h:02d}:{m:02d}")
else:  # Sábado e domingo
    horarios = [f"{h:02d}:{m:02d}" for h in range(8, 20) for m in (0, 30)]
horario = st.selectbox("Horário", horarios)
barbeiro = st.selectbox("Escolha o barbeiro", barbeiros + ["Sem preferência"])
servicos_selecionados = st.multiselect("Serviços", list(servicos.keys()))

# Exibir os preços com o símbolo R$
servicos_com_preco = {servico: f"R$ {preco}" for servico, preco in servicos.items()}
st.write("Preços dos serviços:")
for servico, preco in servicos_com_preco.items():
    st.write(f"{servico}: {preco}")

# Validação dos serviços selecionados
if st.button("Confirmar Agendamento"):
    if nome and telefone and servicos_selecionados:
        if "Sem preferência" in barbeiro:
            # Verifica se ambos os barbeiros estão ocupados
            if not verificar_disponibilidade(data, horario, barbeiros[0]) and not verificar_disponibilidade(data, horario, barbeiros[1]):
                st.error("Horário indisponível para todos os barbeiros. Por favor, selecione outro horário.")
            else:
                # Seleciona um barbeiro aleatoriamente que esteja disponível
                barbeiros_disponiveis = [b for b in barbeiros if verificar_disponibilidade(data, horario, b)]
                if barbeiros_disponiveis:
                    barbeiro = random.choice(barbeiros_disponiveis)
                    if "Barba" in servicos_selecionados and any(corte in servicos_selecionados for corte in ["Tradicional", "Social", "Degradê", "Navalhado"]):
                        if verificar_disponibilidade_horario_seguinte(data, horario, barbeiro):
                            resumo = f"""
                            Nome: {nome}
                            Telefone: {telefone}
                            Data: {data}
                            Horário: {horario}
                            Barbeiro: {barbeiro}
                            Serviços: {', '.join(servicos_selecionados)}
                            """
                            salvar_agendamento(data, horario, nome, telefone, servicos_selecionados, barbeiro)
                            # Bloquear o horário seguinte para o barbeiro selecionado
                            horario_seguinte = (datetime.strptime(horario, '%H:%M') + timedelta(minutes=30)).strftime('%H:%M')
                            salvar_agendamento(data, horario_seguinte, "BLOQUEADO", "BLOQUEADO", ["BLOQUEADO"], barbeiro)
                            enviar_email("Agendamento Confirmado", resumo)
                            st.success("Agendamento confirmado com sucesso! Horário seguinte bloqueado.")
                            st.info("Resumo do agendamento:\n" + resumo)
                        else:
                            st.error("O horário seguinte já está ocupado. Por favor, escolha outro horário.")
                    else:
                        resumo = f"""
                        Nome: {nome}
                        Telefone: {telefone}
                        Data: {data}
                        Horário: {horario}
                        Barbeiro: {barbeiro}
                        Serviços: {', '.join(servicos_selecionados)}
                        """
                        salvar_agendamento(data, horario, nome, telefone, servicos_selecionados, barbeiro)
                        enviar_email("Agendamento Confirmado", resumo)
                        st.success("Agendamento confirmado com sucesso!")
                        st.info("Resumo do agendamento:\n" + resumo)
                else:
                    st.error("Horário indisponível para todos os barbeiros. Por favor, selecione outro horário.")
        else:
            if verificar_disponibilidade(data, horario, barbeiro):
                if "Barba" in servicos_selecionados and any(corte in servicos_selecionados for corte in ["Tradicional", "Social", "Degradê", "Navalhado"]):
                    if verificar_disponibilidade_horario_seguinte(data, horario, barbeiro):
                        resumo = f"""
                        Nome: {nome}
                        Telefone: {telefone}
                        Data: {data}
                        Horário: {horario}
                        Barbeiro: {barbeiro}
                        Serviços: {', '.join(servicos_selecionados)}
                        """
                        salvar_agendamento(data, horario, nome, telefone, servicos_selecionados, barbeiro)
                        # Bloquear o horário seguinte para o barbeiro selecionado
                        horario_seguinte = (datetime.strptime(horario, '%H:%M') + timedelta(minutes=30)).strftime('%H:%M')
                        salvar_agendamento(data, horario_seguinte, "BLOQUEADO", "BLOQUEADO", ["BLOQUEADO"], barbeiro)
                        enviar_email("Agendamento Confirmado", resumo)
                        st.success("Agendamento confirmado com sucesso! Horário seguinte bloqueado.")
                        st.info("Resumo do agendamento:\n" + resumo)
                    else:
                        st.error("O horário seguinte já está ocupado. Por favor, escolha outro horário.")
                else:
                    resumo = f"""
                    Nome: {nome}
                    Telefone: {telefone}
                    Data: {data}
                    Horário: {horario}
                    Barbeiro: {barbeiro}
                    Serviços: {', '.join(servicos_selecionados)}
                    """
                    salvar_agendamento(data, horario, nome, telefone, servicos_selecionados, barbeiro)
                    enviar_email("Agendamento Confirmado", resumo)
                    st.success("Agendamento confirmado com sucesso!")
                    st.info("Resumo do agendamento:\n" + resumo)
            else:
                st.error("O horário escolhido já está ocupado. Por favor, selecione outro horário ou veja outro barbeiro.")
    else:
        st.error("Por favor, preencha todos os campos e selecione pelo menos 1 serviço.")

# Aba de Cancelamento
st.subheader("Cancelar Agendamento")
telefone_cancelar = st.text_input("Telefone para Cancelamento")
horario_cancelar = st.selectbox("Horário do Agendamento", horarios)

if st.button("Cancelar Agendamento"):
    with st.spinner("Processando cancelamento..."):
        cancelado = cancelar_agendamento(data, horario_cancelar, telefone_cancelar)
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
            st.success("Agendamento cancelado com sucesso!")
            st.info("Resumo do cancelamento:\n" + resumo_cancelamento)
        else:
            st.error("Não há agendamento para o telefone informado nesse horário.")
            