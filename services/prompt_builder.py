from __future__ import annotations
import json, re
from typing import Any, Dict, List, Tuple
from openai import OpenAI
from dotenv import load_dotenv
import os

# Load environment variables
load_dotenv()

# ────────────────────────────────────────────────────────────────────────────────
# Public API
# ────────────────────────────────────────────────────────────────────────────────

def build_universal_sales_system_message(
    combined_text: str,
    *,
    welcome_message: str | None = "",
    model: str = "gpt-4o",
    agent_name: str | None = "SalesMate",
    agent_gender: str | None = "male"
) -> str:
    """
    Analyze extracted document content and generate intelligent FAQs for a voice agent.
    Works across ANY industry: Healthcare, Insurance, Real Estate, E-commerce, Banking, etc.
    
    This function:
    1. Analyzes the document to identify industry and structure
    2. Extracts company/product/service information
    3. Generates intelligent FAQs from the data
    4. Creates a clean, queryable knowledge base
    
    Returns the fully rendered system prompt.
    """

    api_key = os.getenv('OPENAI_API_KEY')
    if not api_key:
        raise ValueError("OPENAI_API_KEY environment variable is not set.")
    print("welcome_message:", welcome_message)
    
    client = OpenAI(api_key=api_key)

    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 1: Analyze Document Structure & Extract Company Info
    # ═══════════════════════════════════════════════════════════════════════════
    print("🔍 Step 1: Analyzing document structure and industry...")
    
    analysis_prompt = f"""
    You are an expert at analyzing business documents. Analyze the following document STRICTLY based on what is written.

    RULES:
    - ONLY extract information that is EXPLICITLY written in the document.
    - If a field is not clearly present, DO NOT include it in the JSON response.
    - For industry: ONLY select if you see clear industry-specific terminology (e.g., "policy/premium" for insurance, "BHK/sqft" for real estate, "course/semester" for education).
    - If industry is unclear, use "general_sales".
    - DO NOT guess or assume anything.

    EXTRACT (only if clearly present):
    1. INDUSTRY: Detect ONLY from explicit terminology. Options: healthcare, insurance, real_estate, banking, ecommerce, telecom, education, hospitality, automobile, pharmaceutical, travel, retail, technology, manufacturing, logistics, food_beverage, fitness_wellness, legal_services, recruitment, general_sales
    
    2. COMPANY NAME: Look for letterhead, header, footer, "About Us", contact section. If not found, omit this field.
    
    3. COMPANY ADDRESS/CONTACT: Only if explicitly written.
    
    4. HAS_EXPLICIT_FAQS: Set true ONLY if you see "FAQ", "Q:", "Q&A", "Frequently Asked", numbered questions with answers.

    DOCUMENT:
    {combined_text[:40000]}
    
    ADDITIONAL CONTEXT (if any):
    {welcome_message if welcome_message else "None provided"}

    Return a JSON object with ONLY the fields you can confidently extract:
    {{
        "industry": "detected industry or general_sales",
        "company_name": "only if found",
        "company_address": "only if found",
        "company_contact": "only if found",
        "company_description": "only if clear from document",
        "business_type": "products | services | other",
        "data_type": "pricing_catalog | faqs | brochure | mixed | other",
        "key_entities": ["list of main fields found"],
        "has_explicit_faqs": true/false,
        "data_summary": "brief summary",
        "agent_name": "Priority: extracted from ADDITIONAL CONTEXT, if not found then from document then default to '{agent_name}' if not found"
    }}
    IMPORTANT: 
    - Omit any field where you would have to guess. Only include what is clearly written.
    - For "agent_name" field (ALWAYS include):
      * FIRST: Check ADDITIONAL CONTEXT (welcome message) for agent/representative name
      * SECOND: Check DOCUMENT for agent/representative name in signature, contact, or introduction
      * THIRD: If none found, use default "{agent_name}"
      
    - The "agent_name" field must ALWAYS be included in the response with one of the above values.
    """

    analysis_resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "You are a business document analysis expert. Return only valid JSON."},
            {"role": "user", "content": analysis_prompt}
        ],
        temperature=0.1,
        max_tokens=1000
    )
    
    analysis_json = _clean_json_response(analysis_resp.choices[0].message.content)
    try:
        analysis = json.loads(analysis_json)
        
        # Use defaults for missing fields
        industry = analysis.get('industry', 'general_sales')
        if industry in ['other', 'unknown', '']:
            industry = 'general_sales'
        analysis['industry'] = industry
        
        print(f"✅ Document Analysis: Industry={industry}")
        print(f"   Type={analysis.get('data_type', 'unknown')}, Has FAQs={analysis.get('has_explicit_faqs', False)}")
        if analysis.get('data_summary'):
            print(f"   Summary: {analysis.get('data_summary', '')[:150]}...")
        
        # Warn if critical info is missing
        if 'company_name' not in analysis:
            print("⚠️ WARNING: Company name not found in document. Using fallback.")
        if industry == 'general_sales':
            print("ℹ️ INFO: Using general sales approach (industry not specifically detected).")
    except:
        analysis = {
            "industry": "general_sales",
            "data_type": "unknown", 
            "business_type": "other",
            "key_entities": [], 
            "has_explicit_faqs": False,
            "data_summary": ""
        }
        print("⚠️ Could not parse analysis, using defaults")

    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 2: Generate or Extract Intelligent FAQs (Industry-Adaptive)
    # ═══════════════════════════════════════════════════════════════════════════
    print("🧠 Step 2: Processing FAQs...")
    
    has_faqs = analysis.get("has_explicit_faqs", False)
    explicit_faq_count = analysis.get("explicit_faq_count", 0)
    if not has_faqs and analysis.get("data_type") == "faqs":
        has_faqs = True
        
    if has_faqs:
        print(f"   -> Explicit FAQs detected. Extracting as-is...")
        faq_processing_prompt = f"""
Extract ALL FAQ/Q&A content from the document below.

RULES:
1. Copy the questions and answers EXACTLY as written in the document.
2. Do NOT reformat, rephrase, or translate unless they are in a non-Hindi/English language.
3. Keep the original structure - if it says "Q:" keep "Q:", if it says "Question:" keep that.
4. Include ALL FAQs found, don't skip any.
5. If FAQs are in English, you may translate to Hindi/Hinglish but keep the meaning EXACT.

DOCUMENT:
{combined_text[:40000]}

OUTPUT: Just paste all the FAQs as they appear. Preserve original formatting.
"""
        faq_resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "Extract FAQ content exactly as written. Do not modify or embellish."},
                {"role": "user", "content": faq_processing_prompt}
            ],
            temperature=0.0,
            max_tokens=8000
        )
        generated_faqs = faq_resp.choices[0].message.content.strip()
        
        # If document has substantial additional data beyond FAQs, supplement with generated FAQs
        if has_faqs and analysis.get("data_type") in ["mixed", "brochure", "pricing_catalog", "other"]:
            if len(combined_text) > 15000:
                print("   -> Document has additional data. Generating supplementary FAQs...")
            supplement_prompt = f"""
The document has existing FAQs (shown below). Generate ADDITIONAL FAQs from OTHER data in the document that is NOT covered by existing FAQs.

EXISTING FAQs:
{generated_faqs}

FULL DOCUMENT:
{combined_text[:35000]}

RULES:
1. Generate 40-60 NEW FAQs covering data NOT in existing FAQs.
2. Use ONLY data explicitly present in the document.
3. Include EXACT numbers, prices, names.
4. Format: Q: [Hindi/Hinglish] A: [Hindi/Hinglish with exact data]

---
"""
            supp_resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "Generate supplementary FAQs from document data not covered by existing FAQs."},
                    {"role": "user", "content": supplement_prompt}
                ],
                temperature=0.1,
                max_tokens=1000
            )
            supplementary = supp_resp.choices[0].message.content.strip()
            generated_faqs = f"{generated_faqs}\n\n---\n\n{supplementary}"

    else:
        print("   -> No explicit FAQs. Generating from document data...")
        
        # ──────────────────────────────────────────────────────────────────────────────
        # Generate Industry-Specific FAQ Strategies
        # ──────────────────────────────────────────────────────────────────────────────
        
        industry = analysis.get('industry', 'unknown')
        
        # PASS 1: Core Offerings & Pricing
        print(f"      -> Pass 1: {industry.title()} Offerings & Pricing...")
        
        # Industry-specific question templates
        industry_questions = {
            "healthcare": [
                "What treatments/services are available?",
                "What are the consultation charges?",
                "Which doctors/specialists are available?",
                "What are the package prices?",
                "Do you offer home healthcare services?"
            ],
            "insurance": [
                "What insurance plans do you offer?",
                "What is the premium for [plan]?",
                "What is covered in [policy]?",
                "What is the claim process?",
                "What are the eligibility criteria?"
            ],
            "banking": [
                "What types of accounts do you offer?",
                "What are the interest rates?",
                "What are the loan options?",
                "What is the minimum balance requirement?",
                "What are the processing fees?"
            ],
            "ecommerce": [
                "What products are available?",
                "What is the price of [product]?",
                "Do you offer discounts?",
                "What are the delivery charges?",
                "What is the return policy?"
            ],
            "education": [
                "What courses do you offer?",
                "What is the course fee?",
                "What is the course duration?",
                "Do you provide placement assistance?",
                "What are the admission requirements?"
            ],
            "travel": [
                "What travel packages are available?",
                "What is the cost of [destination] package?",
                "What is included in the package?",
                "What are the visa requirements?",
                "Do you offer customized packages?"
            ],
            "real_estate": [
                "What properties are available in [location]?",
                "What is the price of [property type]?",
                "What are the available configurations (BHK)?",
                "Is it ready to move or under construction?",
                "What amenities are included?"
            ],
            "technology": [
                "What software/services do you offer?",
                "What are the pricing plans?",
                "What features are included?",
                "Do you offer free trials?",
                "What is the implementation timeline?"
            ],
            "retail": [
                "What products do you sell?",
                "What are the store timings?",
                "Do you have ongoing offers?",
                "What is the warranty policy?",
                "Do you offer home delivery?"
            ],
            "fitness_wellness": [
                "What memberships do you offer?",
                "What are the membership fees?",
                "What facilities are available?",
                "Do you have personal trainers?",
                "What are the gym timings?"
            ],
            "food_beverage": [
                "What items are on the menu?",
                "What are the prices?",
                "Do you offer home delivery?",
                "What are the operating hours?",
                "Do you have vegetarian options?"
            ],
            "logistics": [
                "What shipping services do you offer?",
                "What are the delivery charges?",
                "What is the delivery timeline?",
                "Do you offer tracking?",
                "What is the weight limit?"
            ],
            "general_sales": [
                "What products/services do you offer?",
                "What are the prices?",
                "What features are included?",
                "What are the terms and conditions?",
                "How can I make a purchase?"
            ]
        }
        
        default_questions = [
            "What products/services are available?",
            "What are the prices?",
            "What features/benefits are included?",
            "What are the terms and conditions?",
            "How can I get started?"
        ]
        
        question_focus = industry_questions.get(industry, default_questions)
        
        prompt_pass_1 = f"""
You are a sales expert creating FAQs from document data. Generate FAQs focusing on **CORE OFFERINGS, PRICING, PLANS/PACKAGES/PRODUCTS, and KEY FEATURES**.

INDUSTRY: {industry.upper()}

DOCUMENT DATA:
{combined_text[:40000]}

CRITICAL RULES:
1. Generate 40-70 detailed FAQs covering all aspects of offerings and pricing.
2. Use ONLY information explicitly present in the document.
3. Include EXACT numbers, prices, names, percentages from the document.
4. Do NOT round numbers (if document says ₹4,999 write ₹4,999 not ₹5,000).
5. Do NOT invent features, benefits, or details not in the document.
6. If something is unclear in the document, DO NOT include it.
7. Cover EVERY product/service/plan mentioned in the document.

QUESTION FOCUS:
{chr(10).join([f"   - {q}" for q in question_focus[:5]])}

FORMAT:
Q: [Question in Hindi/Hinglish]
A: [Answer in Hindi/Hinglish with EXACT data from document]

---

OUTPUT: Only FAQs. No introductions. Generate as many as the document data supports (aim for 50-70).
"""
        resp_1 = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": f"You are a {industry} industry expert. Output only the FAQs."},
                {"role": "user", "content": prompt_pass_1}
            ],
            temperature=0.1,
            max_tokens=500
        )
        faqs_1 = resp_1.choices[0].message.content.strip()

        # PASS 2: Process, Features & Specifications
        print("      -> Pass 2: Process, Features & Terms...")
        
        prompt_pass_2 = f"""
You are a sales expert creating FAQs from document data. Generate FAQs focusing on **PROCESS/PROCEDURES, TERMS & CONDITIONS, ELIGIBILITY, and TIMELINES**.

INDUSTRY: {industry.upper()}

DOCUMENT DATA:
{combined_text[:40000]}

CRITICAL RULES:
1. Generate 50-70 detailed FAQs covering processes, eligibility, and terms.
2. Use ONLY information explicitly present in the document.
3. Include EXACT requirements, timelines, conditions from the document.
4. Do NOT assume or invent eligibility criteria not stated.
5. Do NOT repeat pricing/offering questions from Pass 1.
6. If process details are unclear, DO NOT guess.
7. Cover comparison questions, "which is better" questions, and specific scenarios.

QUESTION FOCUS:
- "What is the process for [service/application]?"
- "What documents are required?"
- "What are the terms and conditions?"
- "What is the timeline/duration?"
- "Who is eligible for [service/plan]?"
- "[Option A] aur [Option B] mein kya farak hai?"
- "Sabse sasta/best option kaunsa hai?"

FORMAT:
Q: [Question in Hindi/Hinglish]
A: [Answer in Hindi/Hinglish with EXACT data from document]

---

OUTPUT: Only FAQs. No introductions. Generate as many as the document data supports (aim for 50-70).
"""
        resp_2 = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": f"You are a {industry} industry expert. Output only the FAQs."},
                {"role": "user", "content": prompt_pass_2}
            ],
            temperature=0.1,
            max_tokens=500
        )
        faqs_2 = resp_2.choices[0].message.content.strip()

        generated_faqs = f"{faqs_1}\n\n---\n\n{faqs_2}"

    
    print(f"✅ Generated/Extracted FAQs ({len(generated_faqs)} chars)")

    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 3: Generate Data Summary for Quick Reference
    # ═══════════════════════════════════════════════════════════════════════════
    print("📊 Step 3: Generating industry-adaptive data summary...")
    
    summary_prompt = f"""
    Based on this {analysis.get('industry', 'business')} industry data, create a CONCISE summary that covers:

    1. KEY OFFERINGS: Main products/services/plans/packages
    2. PRICE RANGE: Minimum to maximum prices (if applicable)
    3. KEY FEATURES/BENEFITS: Important specifications, coverage, or features
    4. ELIGIBILITY/REQUIREMENTS: Any criteria, terms, or prerequisites
    5. PROCESS/TIMELINE: How to avail, application process, or delivery timelines
    6. KEY HIGHLIGHTS: 3-5 notable offers, benefits, or unique selling points

    DATA:
    {combined_text[:40000]}

    Format your response as a structured summary (NOT a table), like:

    **Key Offerings:** ...
    **Price Range:** ... (if applicable)
    **Features/Benefits:** ...
    **Eligibility/Requirements:** ...
    **Process/Timeline:** ...
    **Highlights:** 
    - [Most affordable option with details]
    - [Premium/comprehensive option with details]
    - [Unique selling point or benefit]
    """

    summary_resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "You are a business data analyst. Create concise, structured summaries."},
            {"role": "user", "content": summary_prompt}
        ],
        temperature=0.1,
        max_tokens=1500
    )
    
    data_summary = summary_resp.choices[0].message.content.strip()
    print(f"✅ Generated summary ({len(data_summary)} chars)")

    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 4: Build Final System Prompt
    # ═══════════════════════════════════════════════════════════════════════════
    print("📝 Step 4: Building final system prompt...")
    final_agent_name = analysis.get('agent_name', agent_name)
    
    # Handle company name - only use if actually found
    company_name = analysis.get("company_name")
    company_known = company_name is not None and company_name not in ["Unknown", ""]
    
    industry = analysis.get('industry', 'general_sales')
    industry_display = industry.replace('_', ' ').title()
    
    # Prepare identity responses based on whether company is known
    if company_known:
        identity_who = f"Main {final_agent_name} hoon, {company_name} ki taraf se aapki madad ke liye."
        identity_why = f"Main {final_agent_name} hoon aur {company_name} ki taraf se aapko jaankari aur madad dene ke liye call kar raha hoon."
    else:
        identity_who = f"Main {final_agent_name} hoon, aapki madad ke liye yahan hoon."
        identity_why = f"Main {final_agent_name} hoon aur aapko jaankari aur madad dene ke liye call kar raha hoon."
    
    # Build company details section - only include what was found
    company_details = f"- Company: {company_name}\n- Industry: {industry_display}"
    if analysis.get('company_address'):
        company_details += f"\n- Address: {analysis['company_address']}"
    if analysis.get('company_contact'):
        company_details += f"\n- Contact: {analysis['company_contact']}"
    if analysis.get('company_description'):
        company_details += f"\n- About: {analysis['company_description']}"
    
    SYSTEM_PROMPT = f"""You are a professional, Hindi-speaking sales assistant for voice-based customer interactions.
Your name is {final_agent_name}. You are a {agent_gender} representative assisting customers.

═══════════════════════════════════════════════════════════════════════════════
CRITICAL GUARDRAILS (MUST FOLLOW)
═══════════════════════════════════════════════════════════════════════════════
- You can ONLY use information from the KNOWLEDGE BASE and DATA SUMMARY below.
- NEVER invent, guess, or hallucinate ANY information (names, prices, features, etc.).
- If you don't know something, say so politely. Do NOT make things up.
- If company name is not specified, refer to it as "humare yahan" or "humare organization".

SOURCE PRIORITY (Follow this order):
1. FIRST: Check KNOWLEDGE BASE (FAQs) for exact or similar question → Use that answer
2. SECOND: Check DATA SUMMARY for relevant information → Formulate answer from it
3. THIRD: If not found in either → Say "Maaf kijiye, yeh jaankari mere paas nahi hai"
   NEVER go to Step 4 (making things up). There is no Step 4.

COMPANY DETAILS:
{company_details}

═══════════════════════════════════════════════════════════════════════════════
DATA SUMMARY (Use this for quick reference)

{data_summary}

═══════════════════════════════════════════════════════════════════════════════
KNOWLEDGE BASE (FAQs - Use these to answer customer queries)

{generated_faqs}

═══════════════════════════════════════════════════════════════════════════════
RESPONSE GUIDELINES
═══════════════════════════════════════════════════════════════════════════════

1. LANGUAGE & TONE: Natural Hindi/Hinglish, friendly, conversational. Use respectful fillers (Ji, Sir/Ma'am). Avoid jargon unless asked. Adapt terminology to the industry (e.g., "policy" for insurance, "treatment" for healthcare, "course" for education).

2. ANSWER STYLE: Keep answers concise (2-4 sentences). Address the question first, add specifics (prices/features/plans/eligibility). Offer 2-3 best options from the knowledge base, then ask if more detail is needed.

   HOW TO FIND ANSWERS:
   a) Search FAQs above for exact or similar question → Use that answer verbatim
   b) If not in FAQs, check DATA SUMMARY → Formulate answer from it
   c) If not in either → Politely say you don't have that information
   d) NEVER combine general knowledge with document data

3. STRICT KNOWLEDGE BOUNDARY (CRITICAL):
   - ONLY answer from the provided KNOWLEDGE BASE and DATA SUMMARY above.
   - If information is NOT in the knowledge base, say: "Maaf kijiye, yeh jaankari mere paas nahi hai. Kya main kisi aur cheez mein madad kar sakta hoon?"
   - NEVER invent, assume, or hallucinate company names, product names, prices, features, or any other details.
   - NEVER use general knowledge or external information.
   - If asked about something not covered, politely redirect to what you DO know.

4. BACKCHANNELS & INTERRUPTIONS: Treat "haan/hmm/acha/theek hai/ok" as backchannel only. If user interrupts with a new question, drop the old thread and answer the new one. If repeated interruptions, keep replies shorter and focused on the last ask.

5. ABUSIVE LANGUAGE: Stay calm; give one polite warning, then a firmer warning; stop only if abuse continues.

6. BACKGROUND NOISE (Semantic VAD): Ignore TV/other voices/short fillers unless it is a clear question. Focus on the primary caller; wait if uncertain.

7. IDENTITY / COMPANY QUESTIONS:
   - "Who are you?" / "Aap kaun ho?": "{identity_who}"
   - "Why did you call?" / "Call kyun kiya?": "{identity_why}"
   - If asked company details you don't know: "Maaf kijiye, yeh specific detail mere paas nahi hai."

8. INDUSTRY-SPECIFIC COURTESY:
   - Healthcare: Show empathy, use terms like "treatment", "consultation", "doctor"
   - Insurance: Be clear about coverage, use terms like "policy", "premium", "claim"
   - Banking: Be precise about numbers, use terms like "account", "loan", "interest rate"
   - Education: Be encouraging, use terms like "course", "admission", "placement"
   - Travel: Be enthusiastic, use terms like "package", "destination", "itinerary"
   - Real Estate: Be informative, use terms like "property", "location", "BHK", "possession"
   - Technology: Be helpful, use terms like "software", "features", "subscription", "support"
   - Retail/E-commerce: Be friendly, use terms like "product", "offer", "delivery", "return"
   - Fitness: Be motivating, use terms like "membership", "trainer", "workout", "health"
   - Food: Be warm, use terms like "menu", "order", "delivery", "taste"
   - General: Adapt to context, stay professional and helpful

9. UNCERTAINTY HANDLING:
   - If you're not 100% sure about an answer, say: "Iske baare mein main confirm karke batata hoon" instead of guessing.
   - Never make up statistics, percentages, or specific numbers not in the knowledge base.
   
10. CRITICAL: OUTBOUND CALL CONTEXT:
- **YOU are calling the customer** - this is an OUTBOUND sales call, NOT an inbound inquiry.
- The customer did NOT call you - YOU initiated this call to present your products/services.
- Act as a proactive sales agent who **showcases and presents**, NOT someone who asks "aapko kya chahiye?" or "kaise madad kar sakta hoon?"

11. SALES APPROACH - Proactive Outbound Calling:
   **YOUR ROLE:**
   1. **Present/Showcase**: Proactively introduce products, services, and value propositions
   2. **Assist with Doubts**: Address questions/concerns helpfully
   3. **Guide Forward**: Lead the conversation toward the offer
   
   **DO THIS:**
   - Lead with 1-2 key benefits + 1 clear offer
   - Use showcase language:
     * "Main aapko batana chahta hoon ki humare paas..." (I want to tell you about...)
     * "Ek baat jo aapke liye helpful ho sakti hai..." (Something helpful for you...)
     * "Hum aapko ye offer de rahe hain..." (We're offering you this...)
   - Ask ONE specific question (budget/timeframe) instead of open-ended queries
   - After answering doubts, immediately continue presenting value
   
   **DO NOT:**
   - Ask generic questions: "Kya aapko koi help chahiye?" or "Aap kya janna chahte hain?"
   - Wait for customer to express needs - YOU drive the conversation
   - Act like support staff waiting for requests
═══════════════════════════════════════════════════════════════════════════════
CALL TERMINATION RULES

You must not end a call unless intent is clear. Never hang up due to silence, background noise, or confusion.

WHEN TO CONSIDER ENDING (detect intent):
- Explicit goodbye/termination (e.g., "bye", "bas", "disconnect", "khatam", "nothing else").
- A clear negative ("no / nahi / no thank you") that directly answers your closing-type question.

MANDATORY CONFIRMATION (always before hangup):
- Ask: "Theek hai. Kya main call abhi samaapt kar doon?" (or EN: "Would you like me to disconnect the call now?").
- Terminate only if the next reply is clearly affirmative ("haan/yes/please disconnect/ji").

NEVER terminate when:
- "No / nahi" is about topic preference (not end intent).
- There is silence, background noise, partial speech, or ambiguity.
- Caller asks to repeat/clarify or seems engaged/undecided.

DECISION RULE:
- If uncertain at any step, do NOT terminate; continue helping.

EXAMPLE — Correct:
You: "Is there anything else I can help you with?"
User: "No, that's all. Thank you."
You: "Theek hai. Kya main call abhi samaapt kar doon?"
User: "Haan, please."
→ Then give a polite goodbye and end.

EXAMPLE — Do NOT end:
User: "No, I want premium plans." (topic preference) → Continue conversation.
"""

    # Enforce final prompt cap (~32k chars) by trimming FAQs first if needed
    MAX_PROMPT_CHARS = 50000
    if len(SYSTEM_PROMPT) > MAX_PROMPT_CHARS:
        overflow = len(SYSTEM_PROMPT) - MAX_PROMPT_CHARS
        if overflow > 0 and generated_faqs:
            trimmed_faqs = generated_faqs[:-overflow] if overflow < len(generated_faqs) else ""
            SYSTEM_PROMPT = SYSTEM_PROMPT.replace(generated_faqs, trimmed_faqs)

    print(f"\n✅ Final system message length: {len(SYSTEM_PROMPT)} characters")
    print(f"📌 Company: {company_name} {'(detected)' if company_known else '(fallback - not detected)'}")
    print(f"📌 Industry: {industry_display}")
    print(f"📌 Data Type: {analysis.get('data_type', 'unknown')}")
    
    return SYSTEM_PROMPT


# ────────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────────

def _clean_json_response(text: str) -> str:
    """Remove markdown code blocks from JSON response."""
    text = text.strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()