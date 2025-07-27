import streamlit as st
import os
import datetime
import json
import re
from typing import List, Dict, Any
from langchain.prompts import PromptTemplate, ChatPromptTemplate
from langchain.chains import LLMChain
from langchain.memory import ConversationBufferMemory
from langchain.schema import HumanMessage, AIMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from dotenv import load_dotenv

load_dotenv()
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
if not GOOGLE_API_KEY:
    st.error("Google API Key not found. Please set it as GOOGLE_API_KEY in your .env file.")
    st.stop()
os.environ["GOOGLE_API_KEY"] = GOOGLE_API_KEY
QUESTIONS_FILE_PATH = 'excel_questions.json'

def load_questions_from_json(file_path: str) -> List[Dict[str, Any]]:
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            questions = json.load(f)
        return questions
    except FileNotFoundError:
        st.error(f"Error: Question bank file '{file_path}' not found. Please ensure 'excel_questions.json' is in the same directory.")
        st.stop()
    except json.JSONDecodeError:
        st.error(f"Error: Could not decode JSON from '{file_path}'. Check file format.")
        st.stop()

class ExcelEvaluator:
    def __init__(self, llm: ChatGoogleGenerativeAI):
        self.llm = llm
        self.evaluation_template = PromptTemplate(
            input_variables=["excel_question", "expected_answer_description", "evaluation_criteria", "candidate_answer"],
            template="""You are an expert Excel interviewer and a highly precise evaluator.
Your task is to critically assess a candidate's answer to an Excel question.

Here's the Excel question asked:
"{excel_question}"

Here is the detailed expected correct answer/description from an expert:
"{expected_answer_description}"

Here are the specific criteria you must use for evaluation:
"{evaluation_criteria}"

Here is the candidate's answer:
"{candidate_answer}"

Based on the above, provide a score from 1 to 5 (where 1 is 'Poor', 3 is 'Adequate', and 5 is 'Excellent').
Then, provide a brief, objective justification for your score, highlighting strengths and weaknesses compared to the expected answer and criteria.

Format your response STRICTLY as a JSON object, without any additional text or markdown formatting (like ```json) outside of the JSON itself.
The JSON object MUST have the following two keys: "score" (integer from 1-5) and "justification" (string).
Example good output: {{"score": 4, "justification": "Good answer."}}
"""
        )

    def evaluate_answer(
        self,
        excel_question: str,
        expected_answer_description: str,
        evaluation_criteria: str,
        candidate_answer: str
    ) -> Dict[str, Any]:
        response_text = ""
        json_string_to_parse = ""
        try:
            evaluation_chain = self.evaluation_template | self.llm
            response_text = evaluation_chain.invoke(
                {
                    "excel_question": excel_question,
                    "expected_answer_description": expected_answer_description,
                    "evaluation_criteria": evaluation_criteria,
                    "candidate_answer": candidate_answer
                }
            )
            if hasattr(response_text, 'content'):
                response_text = response_text.content
            match = re.search(r"```json\n(.*?)```", response_text, re.DOTALL)
            if match:
                json_string_to_parse = match.group(1).strip()
            else:
                json_string_to_parse = response_text.strip()
            evaluation_result = json.loads(json_string_to_parse)
            if "score" not in evaluation_result or "justification" not in evaluation_result:
                raise ValueError("LLM response missing 'score' or 'justification' key.")
            if not isinstance(evaluation_result["score"], int) or not (1 <= evaluation_result["score"] <= 5):
                 raise ValueError("LLM score is not an integer between 1 and 5.")
            return evaluation_result
        except json.JSONDecodeError as e:
            st.error(f"JSON Parsing Error: Could not decode LLM response. Details: {e}")
            st.code(f"Attempted to parse: {json_string_to_parse}\nOriginal LLM Response: {response_text}")
            return {"score": 0, "justification": f"Evaluation error: LLM response not valid JSON. {e}"}
        except ValueError as e:
            st.error(f"Validation Error: LLM response parsing/validation failed. Details: {e}")
            st.code(f"Attempted to parse: {json_string_to_parse}\nOriginal LLM Response: {response_text}")
            return {"score": 0, "justification": f"Evaluation error: LLM response format invalid. {e}"}
        except Exception as e:
            st.error(f"An unexpected error occurred during evaluation: {e}")
            st.code(f"Original LLM Response (if available): {response_text}")
            return {"score": 0, "justification": f"Unexpected evaluation error: {e}"}

class FeedbackGenerator:
    def __init__(self, llm: ChatGoogleGenerativeAI):
        self.llm = llm
        self.feedback_template = PromptTemplate(
            input_variables=["interview_results", "candidate_name"],
            template="""You are an HR professional and an Excel expert.
You have the evaluation results from an Excel mock interview for {candidate_name}.

Here are the interview results in JSON format. Each entry is for a question:
{interview_results}

The report should be clearly formatted using **Markdown** with headings and bullet points.
Ensure the tone is encouraging yet clear about areas needing development.

It must include the following sections in order:
1.  **## Overall Performance Summary**
    * Provide a qualitative assessment of their general Excel proficiency based on the scores across all questions.
2.  **## Strengths**
    * List specific Excel topics or questions where the candidate performed well (e.g., score 4 or 5), using bullet points. Summarize why they did well based on the justifications.
3.  **## Areas for Improvement**
    * List specific Excel topics or question types where the candidate struggled (e.g., score 1, 2, or 3), using bullet points.
    * For each area of improvement, provide 2-3 **actionable suggestions** for learning or practice (e.g., "Practice more with array formulas," "Review VLOOKUP's lookup limitations").
4.  **## Topic-wise Breakdown**
    * Provide a concise summary of performance for each unique Excel topic encountered (e.g., "**Formulas & Functions:** Good grasp, but needs more precision on edge cases.", "**Pivot Tables:** Found basic creation challenging."). Use bullet points for each topic.
5.  **## Overall Score & Proficiency Rating**
    * Calculate the average score across all questions and present it (e.g., "Average Score: X.X / 5").
    * Assign a general proficiency rating based on the overall performance: "Beginner", "Developing", "Intermediate", "Proficient", or "Advanced".
        * Average Score 1.0-1.9: Beginner
        * Average Score 2.0-2.9: Developing
        * Average Score 3.0-3.9: Intermediate
        * Average Score 4.0-4.4: Proficient
        * Average Score 4.5-5.0: Advanced
"""
        )

    def generate_feedback_report(self, interview_results: List[Dict[str, Any]], candidate_name: str = "Candidate") -> str:
        try:
            results_json_str = json.dumps(interview_results, indent=2)
            feedback_chain = self.feedback_template | self.llm
            report = feedback_chain.invoke({"interview_results": results_json_str, "candidate_name": candidate_name})
            if hasattr(report, 'content'):
                report = report.content
            return report
        except Exception as e:
            st.error(f"An error occurred during feedback generation: {e}")
            return "Failed to generate feedback report due to an internal error."

class MockInterviewer:
    def __init__(self, llm: ChatGoogleGenerativeAI, memory: ConversationBufferMemory, questions: List[Dict[str, Any]]):
        self.llm = llm
        self.memory = memory
        self.questions = questions
        self.evaluator = ExcelEvaluator(llm)
        self.feedback_generator = FeedbackGenerator(llm)
        self.interview_history: List[Dict[str, Any]] = []
        self.current_question_index = 0
        self.candidate_name = "Candidate" 
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.transcript_dir = "interview_transcripts"
        self.feedback_dir = "interview_feedback"
        os.makedirs(self.transcript_dir, exist_ok=True)
        os.makedirs(self.feedback_dir, exist_ok=True)
        self.transcript_filename = os.path.join(self.transcript_dir, f"transcript_{timestamp}.txt")
        self.feedback_filename = os.path.join(self.feedback_dir, f"feedback_{timestamp}.txt")
        if 'transcript_file_handle' not in st.session_state:
            st.session_state.transcript_file_handle = open(self.transcript_filename, "w", encoding="utf-8")
        self.transcript_file = st.session_state.transcript_file_handle
        self.system_persona = SystemMessage(
            content="You are an AI-powered Excel interviewer. Your role is to conduct a structured mock interview to assess a candidate's Excel skills. Be professional, clear, and guide the candidate through the process. Do not answer Excel questions yourself, only ask them and acknowledge answers based on the provided instructions."
        )

    def _display_message_to_chat(self, speaker: str, message: str):
        st.session_state.chat_messages.append({"speaker": speaker.lower(), "message": message})
        log_text = f"\n{speaker}: {message}"
        if self.transcript_file and not self.transcript_file.closed:
            self.transcript_file.write(log_text + "\n")
            self.transcript_file.flush()
        else:
            try:
                self.transcript_file = open(self.transcript_filename, "a", encoding="utf-8")
                self.transcript_file.write(log_text + "\n")
                self.transcript_file.flush()
                st.session_state.transcript_file_handle = self.transcript_file
            except Exception as e:
                st.warning(f"Could not re-open transcript file for writing: {e}")

    def _get_ai_response(self, user_input_text: str) -> str:
        messages = [self.system_persona] + self.memory.buffer_as_messages + [HumanMessage(content=user_input_text)]
        ai_response = self.llm.invoke(messages).content
        self.memory.chat_memory.add_user_message(user_input_text)
        self.memory.chat_memory.add_ai_message(ai_response)
        return ai_response

    def start_interview_streamlit(self):
        intro_greeting = "Hello! I'm your AI-powered Excel Mock Interviewer."
        self._display_message_to_chat("AI", intro_greeting)
        intro_prompt = f"Introduce yourself as an AI Excel interviewer. Explain that you will ask {len(self.questions)} questions one by one to assess Excel proficiency. Mention that responses will be evaluated and a comprehensive feedback report will be provided at the end. Do NOT ask if the candidate is ready yet, just provide this full explanation."
        full_introduction = self._get_ai_response(intro_prompt)
        self._display_message_to_chat("AI", full_introduction)
        st.session_state.interview_state = "ask_readiness"
        st.rerun()

    def ask_excel_question_streamlit(self):
        question_data = self.questions[self.current_question_index]
        question_num = self.current_question_index + 1
        total_questions = len(self.questions)
        self._display_message_to_chat("AI", f"--- Question {question_num} of {total_questions}: {question_data['topic']} ---")
        question_phrasing_prompt = f"Please phrase the following Excel question clearly and professionally for a candidate: {question_data['question']}"
        llm_question_output = self.llm.invoke([self.system_persona, HumanMessage(content=question_phrasing_prompt)]).content
        self._display_message_to_chat("AI", llm_question_output)
        st.session_state.current_question_data = question_data 
        st.session_state.interview_state = "await_answer"

    def acknowledge_and_process_answer_streamlit(self, candidate_answer: str, question_data: Dict[str, Any]):
        acknowledgement_message_prompt = f"The candidate has just provided their answer: '{candidate_answer[:100]}...'. Please acknowledge their response briefly and professionally, without evaluating it, and indicate that you are processing it before the next question or concluding."
        acknowledgement_message = self._get_ai_response(acknowledgement_message_prompt)
        self._display_message_to_chat("AI", acknowledgement_message)
        if not candidate_answer:
            self._display_message_to_chat("AI", "It seems you didn't provide an answer. Moving to evaluation for this question.")
            candidate_answer = "[No response provided]"
        evaluation_result = self.evaluator.evaluate_answer(
            excel_question=question_data["question"],
            expected_answer_description=question_data["expected_answer_description"],
            evaluation_criteria=question_data["evaluation_criteria"],
            candidate_answer=candidate_answer
        )
        self.interview_history.append({
            "id": question_data["id"],
            "topic": question_data["topic"],
            "question": question_data["question"],
            "candidate_answer": candidate_answer,
            "evaluation": evaluation_result
        })
        self.current_question_index += 1
        st.session_state.current_question_index = self.current_question_index
        st.session_state.interview_history = self.interview_history 
        if self.current_question_index < len(self.questions):
            self.ask_excel_question_streamlit()
        else:
            self.end_interview_streamlit()

    def end_interview_streamlit(self):
        end_greeting_prompt = "Thank you for completing the mock interview!"
        self._display_message_to_chat("AI", end_greeting_prompt)
        final_message_prompt = "Gracefully conclude the interview. Inform the candidate that you have evaluated all their responses and are now compiling their comprehensive feedback report. Express appreciation for their time."
        final_message = self._get_ai_response(final_message_prompt)
        self._display_message_to_chat("AI", final_message)
        self._display_message_to_chat("AI", "--- Generating Your Feedback Report ---")
        with st.spinner("Compiling your personalized feedback..."):
            report = self.feedback_generator.generate_feedback_report(self.interview_history, candidate_name=self.candidate_name)
        self._display_message_to_chat("AI", "--- Your Performance Feedback ---")
        st.markdown(report)
        try:
            with open(self.feedback_filename, "w", encoding="utf-8") as f:
                f.write(report)
            self._display_message_to_chat("AI", f"Feedback report saved to: {self.feedback_filename}")
        except Exception as e:
            self._display_message_to_chat("AI", f"Error saving feedback report: {e}")
        self._display_message_to_chat("AI", "--- End of Interview ---")
        st.session_state.interview_state = "finished"
        if st.session_state.get('transcript_file_handle') and not st.session_state.transcript_file_handle.closed:
            st.session_state.transcript_file_handle.close()
            st.session_state.pop('transcript_file_handle', None)
        self._display_message_to_chat("AI", f"Transcript saved to: {self.transcript_filename}")
        self.memory.clear()

st.set_page_config(page_title="AI Excel Mock Interviewer", layout="centered")
st.title("🤖 AI Excel Mock Interviewer")

@st.cache_resource
def initialize_resources():
    llm_instance = ChatGoogleGenerativeAI(model="models/gemini-2.0-flash-lite", temperature=0.2)
    memory_instance = ConversationBufferMemory(memory_key="history", return_messages=True)
    questions_list = load_questions_from_json(QUESTIONS_FILE_PATH)
    return llm_instance, memory_instance, questions_list

llm, memory, excel_questions = initialize_resources()

if 'interviewer' not in st.session_state:
    st.session_state.interviewer = MockInterviewer(llm, memory, excel_questions)
    st.session_state.interview_state = "initial"
    st.session_state.current_question_index = 0
    st.session_state.interview_history = []
    st.session_state.candidate_name = "Candidate"
    st.session_state.chat_messages = []

interviewer = st.session_state.interviewer

for message in st.session_state.chat_messages:
    with st.chat_message(message["speaker"]):
        st.markdown(message["message"])

if st.session_state.interview_state == "initial":
    interviewer.start_interview_streamlit()

elif st.session_state.interview_state == "ask_readiness":
    readiness_input = st.chat_input("Are you ready to start the questions now? (yes/no)", key="readiness_input")
    if readiness_input:
        st.session_state.chat_messages.append({"speaker": "user", "message": readiness_input})
        interviewer._display_message_to_chat("Candidate", readiness_input)
        if readiness_input.lower() == 'yes':
            st.session_state.interview_state = "ask_name"
            st.rerun()
        else:
            interviewer._display_message_to_chat("AI", "No problem. We can proceed later. Goodbye!")
            st.session_state.interview_state = "finished"
            st.rerun()

elif st.session_state.interview_state == "ask_name":
    name_input = st.chat_input("Great! What is your name?", key="name_input")
    if name_input:
        st.session_state.chat_messages.append({"speaker": "user", "message": name_input})
        interviewer._display_message_to_chat("Candidate", name_input)
        if name_input:
            interviewer.candidate_name = name_input
            st.session_state.candidate_name = name_input
            interviewer._display_message_to_chat("AI", f"Nice to meet you, {interviewer.candidate_name}! Let's begin.")
        else:
            interviewer._display_message_to_chat("AI", "No problem. We'll proceed. Let's begin.")
        interviewer.current_question_index = st.session_state.current_question_index 
        if interviewer.current_question_index < len(interviewer.questions):
            interviewer.ask_excel_question_streamlit()
        st.rerun()

elif st.session_state.interview_state == "await_answer":
    user_answer = st.chat_input("Your Answer:", key="answer_input")
    if user_answer:
        st.session_state.chat_messages.append({"speaker": "user", "message": user_answer})
        interviewer.acknowledge_and_process_answer_streamlit(user_answer, st.session_state.current_question_data)
        st.rerun()

elif st.session_state.interview_state == "finished":
    st.success("Interview completed! Check the 'interview_transcripts' and 'interview_feedback' folders for your full transcript and feedback reports.")
    if st.button("Start New Interview"):
        st.session_state.clear()
        st.rerun()