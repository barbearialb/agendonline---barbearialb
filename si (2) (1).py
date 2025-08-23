import streamlit as st
import firebase_admin
from firebase_admin import credentials, firestore, auth
from google.cloud.firestore_v1.field_path import FieldPath
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
    "Tradicional",
    "Social",
    "Degradê",
    "Pezim",
    "Navalhado",
    "Barba",
    "Abordagem de visagismo",
    "Consultoria de visagismo",
}

# Lista de serviços para exibição
lista_servicos = servicos

barbeiros = ["Aluizio", "Lucas Borges"]

# Função para enviar e-mail
def enviar_email(assunto, mensagem):
    # Proteção extra para caso as credenciais não carreguem
    if not EMAIL or not SENHA:
        st.warning("Credenciais de e-mail não configuradas. E-mail não enviado.")
        return
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

# SUBSTITUA A FUNÇÃO INTEIRA
def salvar_agendamento(data_str, horario, nome, telefone, servicos, barbeiro):
    if not db:
        st.error("Firestore não inicializado.")
        return False

    try:
        # Converte a data string (que vem do formulário) para um objeto datetime
        data_obj = datetime.strptime(data_str, '%d/%m/%Y')
        
        # Cria o ID do documento no formato correto YYYY-MM-DD
        data_para_id = data_obj.strftime('%Y-%m-%d')
        chave_agendamento = f"{data_para_id}_{horario}_{barbeiro}"
        agendamento_ref = db.collection('agendamentos').document(chave_agendamento)
        
        # Esta é a parte que você perguntou, agora dentro da função principal
        @firestore.transactional
        def update_in_transaction(transaction, doc_ref):
            doc = doc_ref.get(transaction=transaction)
            if doc.exists:
                # Se o documento já existe, a transação falha para evitar agendamento duplo
                raise ValueError("Horário já ocupado por outra pessoa.")
            
            # Se o horário estiver livre, a transação define os novos dados
            transaction.set(doc_ref, {
                'data': data_obj,
                'horario': horario,
                'nome': nome,
                'telefone': telefone,
                'servicos': servicos,
                'barbeiro': barbeiro,
                'timestamp': firestore.SERVER_TIMESTAMP
            })
        
        # Executa a transação
        transaction = db.transaction()
        update_in_transaction(transaction, agendamento_ref)
        return True # Retorna sucesso

    except ValueError as e:
        # Captura o erro "Horário já ocupado" e exibe ao utilizador
        st.error(f"Erro ao agendar: {e}")
        return False
    except Exception as e:
        st.error(f"Erro inesperado ao salvar o agendamento: {e}")
        return False

# Função para cancelar agendamento no Firestore
def cancelar_agendamento(doc_id, telefone_cliente):
    """
    Cancela um agendamento no Firestore de forma segura.
    """
    if not db:
        st.error("Firestore não inicializado.")
        return None
    
    try:
        doc_ref = db.collection('agendamentos').document(doc_id)
        doc = doc_ref.get()

        # PASSO CHAVE: VERIFICA SE O DOCUMENTO EXISTE ANTES DE TUDO
        if not doc.exists:
            st.error(f"Nenhum agendamento encontrado com o ID: {doc_id}")
            return "not_found" # Retorna um código de erro

        agendamento_data = doc.to_dict()
        telefone_no_banco = agendamento_data.get('telefone', '') # Pega o telefone de forma segura

        # Compara os telefones
        if telefone_no_banco.replace(" ", "").replace("-", "") != telefone_cliente.replace(" ", "").replace("-", ""):
            st.error("O número de telefone não corresponde ao agendamento.")
            return "phone_mismatch" # Retorna outro código de erro

        # Se tudo deu certo, deleta e retorna os dados
        doc_ref.delete()
        return agendamento_data

    except Exception as e:
        st.error(f"Ocorreu um erro ao tentar cancelar: {e}")
        return None

# no seu arquivo si (9).py

def desbloquear_horario(data_para_id, horario, barbeiro):
    """
    Desbloqueia um horário usando a data já no formato correto (YYYY-MM-DD).
    """
    if not db:
        st.error("Firestore não inicializado. Não é possível desbloquear.")
        return

    # A função agora recebe a data JÁ no formato YYY-MM-DD, então não precisa converter.
    # As linhas que causavam o erro foram removidas.
    
    chave_bloqueio = f"{data_para_id}_{horario}_{barbeiro}_BLOQUEADO"
    agendamento_ref = db.collection('agendamentos').document(chave_bloqueio)
    
    try:
        # Tenta apagar o documento de bloqueio diretamente.
        # Se o documento não existir, o Firestore não faz nada e não gera erro.
        agendamento_ref.delete()
        # A mensagem de sucesso agora é mostrada na tela principal.

    except Exception as e:
        st.error(f"Erro ao tentar desbloquear o horário seguinte: {e}")

# SUBSTITUA A FUNÇÃO INTEIRA PELA VERSÃO ABAIXO:
def buscar_agendamentos_e_bloqueios_do_dia(data_obj):
    """
    Busca todos os agendamentos do dia usando um prefixo de ID seguro (YYYY-MM-DD).
    """
    if not db:
        st.error("Firestore não inicializado.")
        return set()

    ocupados = set()
    prefixo_id = data_obj.strftime('%Y-%m-%d')

    try:
        # --- SOLUÇÃO DEFINITIVA USANDO order_by, start_at e end_at ---
        docs = db.collection('agendamentos') \
                 .order_by(FieldPath.document_id()) \
                 .start_at([prefixo_id]) \
                 .end_at([prefixo_id + '\uf8ff']) \
                 .stream()
        # --- FIM DA CORREÇÃO ---

        for doc in docs:
            ocupados.add(doc.id)

    except Exception as e:
        st.error(f"Erro ao buscar agendamentos do dia: {e}")

    return ocupados

# A SUA FUNÇÃO, COM A CORREÇÃO DO NOME DA VARIÁVEL
def verificar_disponibilidade_horario_seguinte(data, horario, barbeiro):
    if not db:
        st.error("Firestore não inicializado.")
        return False

    try:
        horario_dt = datetime.strptime(horario, '%H:%M')
        horario_seguinte_dt = horario_dt + timedelta(minutes=30)
        if horario_seguinte_dt.hour >= 20:
            return False 

        horario_seguinte_str = horario_seguinte_dt.strftime('%H:%M')
        data_obj = datetime.strptime(data, '%d/%m/%Y')
        data_para_id = data_obj.strftime('%Y-%m-%d')

        # --- A CORREÇÃO ESTÁ AQUI ---
        # O nome da variável foi padronizado para "chave_agendamento_seguinte"
        chave_agendamento_seguinte = f"{data_para_id}_{horario_seguinte_str}_{barbeiro}"
        agendamento_ref_seguinte = db.collection('agendamentos').document(chave_agendamento_seguinte)
        # --- FIM DA CORREÇÃO ---

        chave_bloqueio_seguinte = f"{data_para_id}_{horario_seguinte_str}_{barbeiro}_BLOQUEADO"
        bloqueio_ref_seguinte = db.collection('agendamentos').document(chave_bloqueio_seguinte)

        doc_agendamento_seguinte = agendamento_ref_seguinte.get()
        doc_bloqueio_seguinte = bloqueio_ref_seguinte.get()

        return not doc_agendamento_seguinte.exists and not doc_bloqueio_seguinte.exists

    except google.api_core.exceptions.RetryError as e:
        st.error(f"Erro de conexão com o Firestore ao verificar horário seguinte: {e}")
        return False
    except Exception as e:
        st.error(f"Erro inesperado ao verificar disponibilidade do horário seguinte: {e}")
        return False
        
# Função para bloquear horário para um barbeiro específico
def bloquear_horario(data, horario, barbeiro):
    if not db:
        st.error("Firestore não inicializado. Não é possível bloquear.")
        return False

    # 1. Converte a string de data "dd/mm/yyyy" para um objeto de data.
    try:
        data_obj = datetime.strptime(data, '%d/%m/%Y')
    except ValueError:
        st.error("Formato de data inválido para bloqueio.")
        return False

    # 2. Usa o objeto de data para criar o ID no formato CORRETO (YYYY-MM-DD).
    data_para_id = data_obj.strftime('%Y-%m-%d')
    chave_bloqueio = f"{data_para_id}_{horario}_{barbeiro}_BLOQUEADO"

    try:
        # 3. Usa a chave correta para criar o documento de bloqueio.
        db.collection('agendamentos').document(chave_bloqueio).set({
            'nome': "BLOQUEADO",
            'telefone': "BLOQUEADO",
            'servicos': ["BLOQUEADO"],
            'barbeiro': barbeiro,
            'data': data_obj,  # Salva o objeto de data no documento
            'horario': horario,
            'agendado_por': 'bloqueio_interno' # Campo para identificar a origem
        })
        return True
    except Exception as e:
        st.error(f"Erro ao bloquear horário: {e}")
        return False

# Interface Streamlit
st.title("Barbearia Lucas Borges - Agendamentos")
st.header("Faça seu agendamento ou cancele")
st.image("https://github.com/barbearialb/sistemalb/blob/main/icone.png?raw=true", use_container_width=True)

# Gerenciamento da Data Selecionada no Session State
if 'data_agendamento' not in st.session_state:
    st.session_state.data_agendamento = datetime.today().date()  # Inicializar como objeto date

if 'date_changed' not in st.session_state:
    st.session_state['date_changed'] = False

def handle_date_change():
    st.session_state.data_agendamento = st.session_state.data_input_widget  # Atualizar com o objeto date
    # verificar_disponibilidade.clear() # Limpar cache se estivesse usando @st.cache_data
    st.session_state['date_changed'] = True # Indica que a data mudou
    # st.rerun() # Força o rerender da página para atualizar a tabela imediatamente (opcional, mas melhora UX)

data_agendamento_obj = st.date_input(
    "Data para visualizar disponibilidade",
    value=st.session_state.data_agendamento, # Usa o valor do session state
    min_value=datetime.today().date(), # Garante que seja um objeto date
    key="data_input_widget",
    on_change=handle_date_change
)

# Atualiza o session state se o valor do widget for diferente (necessário se não usar on_change ou rerun)
if data_agendamento_obj != st.session_state.data_agendamento:
     st.session_state.data_agendamento = data_agendamento_obj
    

# Sempre usa a data do session_state para consistência
# --- Tabela de Disponibilidade ---

# SUAS LINHAS - MANTIDAS EXATAMENTE COMO PEDIU
data_para_tabela = st.session_state.data_agendamento.strftime('%d/%m/%Y')
data_obj_tabela = st.session_state.data_agendamento

st.subheader("Disponibilidade dos Barbeiros")

# 1. CHAMA A FUNÇÃO RÁPIDA UMA ÚNICA VEZ
# Usamos o objeto de data que você já tem
agendamentos_do_dia = buscar_agendamentos_e_bloqueios_do_dia(data_obj_tabela)

# 2. CRIA A VARIÁVEL COM O FORMATO CORRETO PARA O ID
# Esta é a adição importante. Usamos o objeto de data para criar a string YYYY-MM-DD
data_para_id_tabela = data_obj_tabela.strftime('%Y-%m-%d')

# --- O resto da sua lógica de construção da tabela continua, mas usando a variável correta ---
html_table = '<table style="font-size: 14px; border-collapse: collapse; width: 100%; border: 1px solid #ddd;"><tr><th style="padding: 8px; border: 1px solid #ddd; background-color: #0e1117; color: white;">Horário</th>'
for barbeiro in barbeiros:
    html_table += f'<th style="padding: 8px; border: 1px solid #ddd; background-color: #0e1117; color: white; min-width: 120px; text-align: center;">{barbeiro}</th>'
html_table += '</tr>'

dia_da_semana_tabela = data_obj_tabela.weekday()
horarios_tabela = [f"{h:02d}:{m:02d}" for h in range(8, 20) for m in (0, 30)]

for horario in horarios_tabela:
    html_table += f'<tr><td style="padding: 8px; border: 1px solid #ddd; text-align: center;">{horario}</td>'
    for barbeiro in barbeiros:
        status = "Indisponível"
        bg_color = "grey"
        color_text = "white"
        hora_int = int(horario.split(':')[0])

        # A sua lógica de SDJ (mantida igual)
        if horario in ["07:00", "07:30"]:
            dia_do_mes = data_obj_tabela.day
            mes_do_ano = data_obj_tabela.month
            if not (mes_do_ano == 7 and 10 <= dia_do_mes <= 19):
                status = "SDJ"
                bg_color = "#696969"
                html_table += f'<td style="padding: 8px; border: 1px solid #ddd; background-color: {bg_color}; text-align: center; color: {color_text}; height: 30px;">{status}</td>'
                continue

        # 3. A CORREÇÃO CRUCIAL
        # Usamos a nova variável `data_para_id_tabela` para criar a chave
        chave_agendamento = f"{data_para_id_tabela}_{horario}_{barbeiro}"
        chave_bloqueio = f"{chave_agendamento}_BLOQUEADO"

        disponivel = (chave_agendamento not in agendamentos_do_dia) and (chave_bloqueio not in agendamentos_do_dia)

        # A sua lógica de dias da semana (mantida igual)
        if dia_da_semana_tabela < 5:
            dia = data_obj_tabela.day
            mes = data_obj_tabela.month
            intervalo_especial = mes == 7 and 10 <= dia <= 19
            almoco_lucas = not intervalo_especial and (hora_int == 12 or hora_int == 13)
            almoco_aluizio = not intervalo_especial and (hora_int == 12 or hora_int == 13)

            if barbeiro == "Lucas Borges" and almoco_lucas:
                status, bg_color, color_text = "Almoço", "orange", "black"
            elif barbeiro == "Aluizio" and almoco_aluizio:
                status, bg_color, color_text = "Almoço", "orange", "black"
            else:
                status = "Disponível" if disponivel else "Ocupado"
                bg_color = "forestgreen" if disponivel else "firebrick"

        elif dia_da_semana_tabela == 5:
            status = "Disponível" if disponivel else "Ocupado"
            bg_color = "forestgreen" if disponivel else "firebrick"

        elif dia_da_semana_tabela == 6:
            dia = data_obj_tabela.day
            mes = data_obj_tabela.month
            if mes == 7 and 10 <= dia <= 19:
                status = "Disponível" if disponivel else "Ocupado"
                bg_color = "forestgreen" if disponivel else "firebrick"
            else:
                status, bg_color, color_text = "Fechado", "#A9A9A9", "black"
        
        html_table += f'<td style="padding: 8px; border: 1px solid #ddd; background-color: {bg_color}; text-align: center; color: {color_text}; height: 30px;">{status}</td>'
    
    html_table += '</tr>'

html_table += '</table>'
st.markdown(html_table, unsafe_allow_html=True)

# Aba de Agendamento (FORMULÁRIO)
with st.form("agendar_form"):
    st.subheader("Agendar Horário")
    nome = st.text_input("Nome")
    telefone = st.text_input("Telefone")

    # Usar o valor do session state para a data DENTRO do formulário
    # A data exibida aqui será a mesma da tabela, pois ambas usam session_state
    st.write(f"Data selecionada: **{st.session_state.data_agendamento.strftime('%d/%m/%Y')}**")
    data_agendamento_str_form = st.session_state.data_agendamento.strftime('%d/%m/%Y') # String para salvar
    data_obj_agendamento_form = st.session_state.data_agendamento # Objeto date para validações

    # Geração da lista de horários completa para agendamento
    horarios_base_agendamento = [f"{h:02d}:{m:02d}" for h in range(8, 20) for m in (0, 30)]

    barbeiro_selecionado = st.selectbox("Escolha o barbeiro", barbeiros + ["Sem preferência"])

    # Filtrar horários de almoço com base no barbeiro selecionado ou "Sem preferência"
    # (Opcional: Poderia filtrar aqui, mas a validação no submit é mais robusta)
    horarios_disponiveis_dropdown = horarios_base_agendamento # Por enquanto, mostra todos
    # --- Lógica de filtragem complexa poderia entrar aqui ---
    # Mas é mais seguro validar APÓS o submit, pois a disponibilidade pode mudar

    horario_agendamento = st.selectbox("Horário", horarios_disponiveis_dropdown)

    servicos_selecionados = st.multiselect("Serviços", lista_servicos)

    # Exibir os preços com o símbolo R$
    st.write("Serviços disponíveis:")
    for servico in servicos:
        st.write(f"- {servico}")

    submitted = st.form_submit_button("Confirmar Agendamento")

if submitted:
    with st.spinner("Processando agendamento..."):
        # Usar o objeto date diretamente do session state para obter o dia da semana
        dia_da_semana_agendamento = data_obj_agendamento_form.weekday() # 0=Segunda, 6=Domingo
        dia = data_obj_agendamento_form.day
        mes = data_obj_agendamento_form.month
        if dia_da_semana_agendamento == 6:
            if not (mes == 7 and 10 <= dia <= 19):
                st.error("Desculpe, estamos fechados aos domingos.")
                st.stop()

        # <<< MODIFICAÇÃO 2: Verificar se é Domingo ANTES de tudo >>>
        #if dia_da_semana_agendamento == 6:
            #st.error("Desculpe, não realizamos agendamentos aos domingos.")
            #st.stop() # Interrompe a execução do agendamento
        # <<< FIM MODIFICAÇÃO 2 >>>

        # Validações básicas de preenchimento
        if not nome or not telefone or not servicos_selecionados:
            st.error("Por favor, preencha seu nome, telefone e selecione pelo menos um serviço.")
            st.stop()
        if horario_agendamento in ["07:00", "07:30"]:
           dia = data_obj_agendamento_form.day
           mes = data_obj_agendamento_form.month
           
           if not (mes == 7 and 10 <= dia <= 19):
            st.error("Os horários de 07:00 e 07:30 só estão disponíveis entre os dias 11 e 19 de julho.")
            st.stop()

        # --- Validações de Horário de Almoço ---
        hora_agendamento_int = int(horario_agendamento.split(':')[0])
        minuto_agendamento_int = int(horario_agendamento.split(':')[1])

        # Verifica almoço apenas se for dia de semana (0 a 4)
        if dia_da_semana_agendamento < 5:
            dia = data_obj_agendamento_form.day
            mes = data_obj_agendamento_form.month
            
            intervalo_especial = mes == 7 and 10 <= dia <= 19
            almoco_lucas = not intervalo_especial and (hora_agendamento_int == 12 or hora_agendamento_int == 13)
            almoco_aluizio = not intervalo_especial and (hora_agendamento_int == 12 or hora_agendamento_int == 13)

            # Se selecionou barbeiro específico
            if barbeiro_selecionado == "Lucas Borges" and almoco_lucas:
                st.error(f"Lucas Borges está em horário de almoço às {horario_agendamento}. Por favor, escolha outro horário.")
                st.stop()
            elif barbeiro_selecionado == "Aluizio" and almoco_aluizio:
                 st.error(f"Aluizio está em horário de almoço às {horario_agendamento}. Por favor, escolha outro horário.")
                 st.stop()
            # Se selecionou "Sem preferência" e AMBOS estão em almoço nesse horário
            # (Nota: Seus horários de almoço não coincidem, então essa condição específica não será atingida,
            # mas a lógica está aqui caso os horários mudem no futuro)
            elif barbeiro_selecionado == "Sem preferência" and almoco_lucas and almoco_aluizio:
                 st.error(f"Ambos os barbeiros estão em horário de almoço às {horario_agendamento}. Por favor, escolha outro horário.")
                 st.stop()
            # Se selecionou "Sem preferência" e o horário coincide com o almoço de UM deles,
            # o sistema tentará agendar com o outro automaticamente mais abaixo. Não precisa parar aqui.

        # --- Validação de Visagismo ---
        servicos_visagismo = ["Abordagem de visagismo", "Consultoria de visagismo"]
        visagismo_selecionado = any(servico in servicos_selecionados for servico in servicos_visagismo)

        if visagismo_selecionado and barbeiro_selecionado == "Aluizio":
             st.error("Apenas Lucas Borges realiza atendimentos de visagismo. Por favor, selecione Lucas Borges ou remova o serviço de visagismo.")
             st.stop()
        # Se selecionou "Sem preferência" e visagismo, força a seleção para Lucas Borges
        if visagismo_selecionado and barbeiro_selecionado == "Sem preferência":
            barbeiro_final = "Lucas Borges"
            st.info("Serviço de visagismo selecionado. Agendamento direcionado para Lucas Borges.")
        elif barbeiro_selecionado != "Sem preferência":
            barbeiro_final = barbeiro_selecionado
        else:
             barbeiro_final = None # Será definido abaixo

        # --- Lógica de Atribuição e Verificação de Disponibilidade ---
        barbeiros_a_verificar = []
        if barbeiro_final: # Se já foi definido (Visagismo ou escolha específica)
            barbeiros_a_verificar.append(barbeiro_final)
        else: # Se for "Sem preferência" e sem visagismo
            barbeiros_a_verificar = list(barbeiros) # Verifica ambos

# DEPOIS (CORRETO)
        barbeiro_agendado = None
# A data já está como objeto em data_obj_agendamento_form
        data_para_id_form = data_obj_agendamento_form.strftime('%Y-%-m-%d')

        intervalo_especial = False
        if dia_da_semana_agendamento < 5:
            intervalo_especial = (mes == 7 and 10 <= dia <= 19)
            
        for b in barbeiros_a_verificar:
            if not intervalo_especial:
                if b == "Lucas Borges" and (hora_agendamento_int == 12 or hora_agendamento_int == 13):
                    continue # Pula este barbeiro se estiver em almoço
                if b == "Aluizio" and (hora_agendamento_int == 12 or hora_agendamento_int == 13):
                    continue # Pula este barbeiro se estiver em almoço

            chave_agendamento_form = f"{data_para_id_form}_{horario_agendamento}_{b}"
            chave_bloqueio_form = f"{chave_agendamento_form}_BLOQUEADO"

    # Verifica de forma instantânea no conjunto que já foi carregado
            if (chave_agendamento_form not in agendamentos_do_dia) and (chave_bloqueio_form not in agendamentos_do_dia):
                barbeiro_agendado = b
                break # Encontrou um barbeiro disponível

# O resto do seu código a partir daqui continua igual...
        if not barbeiro_agendado:
            st.error(f"Horário {horario_agendamento} indisponível para os barbeiros selecionados/disponíveis. Por favor, escolha outro horário ou verifique a tabela de disponibilidade.")
            st.stop()

        # --- Verificação de Horário Seguinte para Corte+Barba ---
        precisa_bloquear_proximo = False
        corte_selecionado = any(corte in servicos_selecionados for corte in ["Tradicional", "Social", "Degradê", "Navalhado"])
        barba_selecionada = "Barba" in servicos_selecionados

        if corte_selecionado and barba_selecionada:
            if not verificar_disponibilidade_horario_seguinte(data_agendamento_str_form, horario_agendamento, barbeiro_agendado):
                horario_seguinte_dt = datetime.strptime(horario_agendamento, '%H:%M') + timedelta(minutes=30)
                horario_seguinte_str = horario_seguinte_dt.strftime('%H:%M')
                st.error(f"O barbeiro {barbeiro_agendado} não poderá atender para corte e barba, pois já está ocupado no horário seguinte ({horario_seguinte_str}). Por favor, escolha serviços que caibam em 30 minutos ou selecione outro horário/barbeiro.")
                st.stop()
            else:
                precisa_bloquear_proximo = True

        # --- Salvar Agendamento e Bloquear (se necessário) ---
        agendamento_salvo = salvar_agendamento(data_agendamento_str_form, horario_agendamento, nome, telefone, servicos_selecionados, barbeiro_agendado)

        if agendamento_salvo:
            horario_seguinte_bloqueado = False
            if precisa_bloquear_proximo:
                horario_seguinte_dt = datetime.strptime(horario_agendamento, '%H:%M') + timedelta(minutes=30)
                horario_seguinte_str = horario_seguinte_dt.strftime('%H:%M')
                horario_seguinte_bloqueado = bloquear_horario(data_agendamento_str_form, horario_seguinte_str, barbeiro_agendado)
                if not horario_seguinte_bloqueado:
                     st.warning("O agendamento principal foi salvo, mas houve um erro ao bloquear o horário seguinte. Por favor, entre em contato com a barbearia se necessário.")


            # --- Preparar e Enviar E-mail ---
            resumo = f"""
            Nome: {nome}
            Telefone: {telefone}
            Data: {data_agendamento_str_form}
            Horário: {horario_agendamento}
            Barbeiro: {barbeiro_agendado}
            Serviços: {', '.join(servicos_selecionados)}
            """
            enviar_email("Agendamento Confirmado", resumo)

            # --- Mensagem de Sucesso e Rerun ---
            st.success("Agendamento confirmado com sucesso!")
            st.info("Resumo do agendamento:\n" + resumo)
            if horario_seguinte_bloqueado:
                st.info(f"O horário das {horario_seguinte_str} com {barbeiro_agendado} foi bloqueado para acomodar todos os serviços.")

            # Limpar cache (se estivesse usando) e atualizar a página
            # verificar_disponibilidade.clear()
            time.sleep(5) # Pausa para o usuário ler as mensagens
            st.rerun()

        else:
            # Mensagem de erro se salvar_agendamento falhar (já exibida pela função)
            st.error("Não foi possível completar o agendamento. Verifique as mensagens de erro acima ou tente novamente.")


# Aba de Cancelamento
with st.form("cancelar_form"):
    st.subheader("Cancelar Agendamento")
    telefone_cancelar = st.text_input("Telefone usado no Agendamento")
    data_cancelar = st.date_input("Data do Agendamento", min_value=datetime.today().date()) # Usar date()

    # Geração da lista de horários completa para cancelamento
    horarios_base_cancelamento = [f"{h:02d}:{m:02d}" for h in range(8, 20) for m in (0, 30)]

    horario_cancelar = st.selectbox("Horário do Agendamento", horarios_base_cancelamento) # Usa a lista completa

    barbeiro_cancelar = st.selectbox("Barbeiro do Agendamento", barbeiros)
    submitted_cancelar = st.form_submit_button("Cancelar Agendamento")

    if submitted_cancelar:
        if not telefone_cancelar:
            st.error("Por favor, informe o telefone utilizado no agendamento.")
        else:
            with st.spinner("Processando cancelamento..."):
                data_para_id = data_cancelar.strftime('%Y-%m-%d')
                doc_id_cancelar = f"{data_para_id}_{horario_cancelar}_{barbeiro_cancelar}"

                resultado_cancelamento = cancelar_agendamento(doc_id_cancelar, telefone_cancelar)

                if isinstance(resultado_cancelamento, dict):
                    agendamento_cancelado_data = resultado_cancelamento
                    servicos_cancelados = agendamento_cancelado_data.get('servicos', [])
                    corte_no_cancelado = any(corte in servicos_cancelados for corte in ["Tradicional", "Social", "Degradê", "Navalhado"])
                    barba_no_cancelado = "Barba" in servicos_cancelados
                    horario_seguinte_desbloqueado = False

                    if corte_no_cancelado and barba_no_cancelado:
                        horario_agendamento_original = agendamento_cancelado_data['horario']
                        barbeiro_original = agendamento_cancelado_data['barbeiro']
                        data_obj_original = agendamento_cancelado_data['data']

                        horario_seguinte_dt = (datetime.strptime(horario_agendamento_original, '%H:%M') + timedelta(minutes=30))
                        if horario_seguinte_dt.hour < 20:
                            horario_seguinte_str = horario_seguinte_dt.strftime('%H:%M')
                            data_para_id_desbloqueio = data_obj_original.strftime('%Y-%m-%d')
                            desbloquear_horario(data_para_id_desbloqueio, horario_seguinte_str, barbeiro_original)
                            horario_seguinte_desbloqueado = True

        # --- A sua lógica de E-mail e Mensagem de Sucesso (MANTIDA) ---
                    resumo_cancelamento = f"""
                    Agendamento Cancelado:
                    Nome: {agendamento_cancelado_data.get('nome', 'N/A')}
                    Telefone: {agendamento_cancelado_data.get('telefone', 'N/A')}
                    Data: {data_cancelar.strftime('%d/%m/%Y')}
                    Horário: {agendamento_cancelado_data.get('horario', 'N/A')}
                    Barbeiro: {agendamento_cancelado_data.get('barbeiro', 'N/A')}
                    Serviços: {', '.join(agendamento_cancelado_data.get('servicos', []))}
                    """
                    enviar_email("Agendamento Cancelado", resumo_cancelamento)
        
                    st.success("Agendamento cancelado com sucesso!")
                    if horario_seguinte_desbloqueado:
                        st.info("O horário seguinte, que estava bloqueado, foi liberado.")
        
                    time.sleep(5)
                    st.rerun()









