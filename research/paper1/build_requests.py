#!/usr/bin/env python3
"""Build research/paper1/requests.jsonl - the labelled learner-request set for paper 1.

Run once; the JSONL is committed so the experiment never depends on this script.

    python research/paper1/build_requests.py

Labelling rules (also in docs/PAPER1_EXPERIMENT.md):
* gold = the gateways whose artefact the request asks for, explicitly ("make flashcards") or
  through a clear cue ("help me memorise" -> flashcards, "test myself" / "practise" -> quiz,
  "something to listen to" -> podcast, "show me visually" / "animate" -> video,
  "Python program" -> code).
* "explain / what is / how does / teach me / summarise / notes" -> notes. A request that names
  only other artefacts ("make a quiz on X") does NOT get notes. "Explain X with a video" = notes + video.
* explicit = every gold artefact is named in the request (no cue words needed).
* subject = list of acceptable router subjects (topics on a boundary accept several).
* in_scope = a request to learn a technical/scientific topic. History, languages and errands are out
  of scope (gold = []).
* pair = concept id shared by translations / paraphrases of the same intent in other languages
  (cross-lingual consistency).
* e2e = in the end-to-end three-arm set (P4); ablation = in the router-schema ablation subset (P3).
"""
from __future__ import annotations

import json
import random
from pathlib import Path

N, F, Q, P, V, C = "notes", "flashcards", "quiz", "podcast", "video", "code"
ALL6 = [N, F, Q, P, V, C]
PHY, CHE, BIO, MAT, CS = "physics", "chemistry", "biology", "mathematics", "computer_science"
ELN, EE, ME, CE, ES = "electronics", "electrical_engineering", "mechanical_engineering", "civil_engineering", "earth_science"

# (text, gold, explicit, subjects, pair)
EN = [
    ("Explain Ohm's law with a short quiz.", [N, Q], True, [PHY, EE], "c01"),
    ("Make me 10 flashcards on the groups of the periodic table.", [F], True, [CHE], None),
    ("I want an animated video showing how binary search works.", [V], True, [CS], None),
    ("Write Python code that simulates projectile motion and explain it.", [C, N], True, [PHY, CS], None),
    ("Create a podcast where two people discuss how vaccines train the immune system.", [P], True, [BIO], None),
    ("What is photosynthesis?", [N], False, [BIO], None),
    ("Give me study notes, flashcards and a quiz on Newton's three laws of motion.", [N, F, Q], True, [PHY], None),
    ("I have a chemistry exam tomorrow on acids and bases - help me test myself.", [Q], False, [CHE], "c44"),
    ("I commute for an hour every day; I'd like to learn about plate tectonics while driving.", [P], False, [ES], "c43"),
    ("Show me visually why the derivative of x squared is 2x.", [V], False, [MAT], "c17"),
    ("Teach me recursion in Python with examples I can run.", [N, C], False, [CS], "c41"),
    ("Summarise the causes of earthquakes in simple notes.", [N], True, [ES], None),
    ("Make a quiz with 5 questions on the human heart.", [Q], True, [BIO], None),
    ("Create flashcards and a podcast about the water cycle.", [F, P], True, [ES], None),
    ("Explain Kirchhoff's current and voltage laws, with a video and some practice questions.", [N, V, Q], True, [EE, PHY], None),
    ("Write Python code for bubble sort and make a short video explaining how it works.", [C, V], True, [CS], None),
    ("I need everything for learning DNA replication: notes, flashcards, a quiz, a podcast and a video.", [N, F, Q, P, V], True, [BIO], None),
    ("Help me memorise the SI base units.", [F], False, [PHY], None),
    ("How does a transistor work as a switch?", [N], False, [ELN], "c13"),
    ("Write a Python function that computes the GCD with Euclid's algorithm.", [C], True, [MAT, CS], None),
    ("Make an audio lesson on the Krebs cycle.", [P], True, [BIO, CHE], None),
    ("Animate the proof of the Pythagorean theorem.", [V], True, [MAT], "c05"),
    ("Notes and flashcards on the stages of mitosis, please.", [N, F], True, [BIO], None),
    ("Quiz me on the properties of logarithms.", [Q], True, [MAT], None),
    ("Explain how a four-stroke petrol engine works, with an animation.", [N, V], True, [ME], "c20b"),
    ("I'm a beginner. What is an electric current?", [N], False, [PHY, EE], "c09"),
    ("Give me practice questions and flashcards on organic chemistry functional groups.", [Q, F], True, [CHE], None),
    ("Create a two-host podcast about black holes for a 12-year-old.", [P], True, [PHY], None),
    ("Explain beam bending and shear force diagrams for civil engineering students.", [N], False, [CE, ME], None),
    ("Write Python code to plot a sine wave and explain what each line does.", [C, N], True, [MAT, CS], None),
    ("Make a video about how a convex lens forms an image.", [V], True, [PHY], None),
    ("Revision flashcards for the laws of thermodynamics.", [F], True, [PHY, ME, CHE], None),
    ("I learn best by listening - make me something audio about the structure of the atom.", [P], False, [PHY, CHE], None),
    ("Explain the difference between AC and DC current.", [N], False, [EE, PHY], "c22"),
    ("Make a quiz and a video about the planets of the solar system.", [Q, V], True, [PHY, ES, "other"], None),
    ("Implement a stack and a queue in Python, with notes comparing them.", [C, N], True, [CS], "c42"),
    ("Explain the photoelectric effect with notes, a quiz and an animated video.", [N, Q, V], True, [PHY], None),
    ("Short notes on Bernoulli's principle.", [N], True, [PHY, ME], None),
    ("Can you make flashcards for common logic gates and their truth tables?", [F], True, [ELN, CS], "c31"),
    ("Make a podcast and a quiz about climate change and the greenhouse effect.", [P, Q], True, [ES], None),
    ("How do neural networks learn? Show me with code.", [N, C], False, [CS], None),
    ("Test my knowledge of trigonometric identities.", [Q], False, [MAT], "c27"),
    ("I want a video, notes and flashcards about the nitrogen cycle.", [V, N, F], True, [BIO, ES], "c32"),
    ("Explain Big-O notation for an advanced student.", [N], False, [CS], None),
    ("Generate a quiz on the basics of reinforced concrete.", [Q], True, [CE], "c28"),
    ("Create an animated explainer of how a DC motor works and a podcast episode about it.", [V, P], True, [EE, PHY], "c29"),
    ("Write a Python program that solves a quadratic equation.", [C], True, [MAT], "c34"),
    ("Notes, a quiz and code examples for linked lists.", [N, Q, C], True, [CS], None),
    ("What causes the seasons on Earth?", [N], False, [ES, PHY], None),
    ("Make flashcards, a quiz and a podcast about the human digestive system.", [F, Q, P], True, [BIO], "c35"),
    ("Visualise how a Fourier series builds a square wave.", [V], False, [MAT, EE], None),
    ("Give me a complete lesson on Ohm's law: notes, flashcards, quiz, podcast, video and code.", ALL6, True, [PHY, EE], "c37"),
    ("I keep forgetting enzyme names; help me drill them.", [F], False, [BIO], "c19"),
    ("Explain stress and strain with an animation and a few MCQs.", [N, V, Q], True, [ME, CE, PHY], None),
    ("Create a podcast explaining how the internet routes packets.", [P], True, [CS], None),
    ("Make a quiz on chemical bonding: ionic, covalent and metallic.", [Q], True, [CHE], None),
    ("Code a simple simulation of radioactive decay in Python.", [C], True, [PHY], "c38"),
    ("Explain how an op-amp works in an inverting amplifier.", [N], False, [ELN, EE], None),
    ("Notes and an animated video on the electromagnetic spectrum.", [N, V], True, [PHY], None),
    ("Flashcards for key terms in genetics: allele, genotype, phenotype and more.", [F], True, [BIO], None),
    ("Make a video and some Python code that show how matrix multiplication works.", [V, C], True, [MAT], None),
    ("Create a podcast and study notes about volcanoes.", [P, N], True, [ES], None),
    ("Make a 5-question quiz and flashcards on Newton's law of gravitation.", [Q, F], True, [PHY], None),
    ("Explain the working of a hydraulic press.", [N], False, [ME, PHY], None),
    ("Make an animated video about how a capacitor charges and discharges, plus a quiz.", [V, Q], True, [ELN, EE, PHY], "c36"),
    ("Write sorting-algorithm code in Python and quiz me on time complexity.", [C, Q], True, [CS], None),
    ("I'm preparing for a viva on soil mechanics. Help me practise.", [Q], False, [CE], "c40"),
    ("Explain cellular respiration in an audio format I can listen to at the gym, plus flashcards.", [N, P, F], False, [BIO], None),
    ("Notes on the structure of the Earth's interior.", [N], True, [ES], None),
    ("Teach me how a binary adder circuit works with notes, a video and Python code that simulates it.", [N, V, C], True, [ELN, CS], None),
]

HI = [
    ("ओम का नियम एक छोटे क्विज़ के साथ समझाइए।", [N, Q], True, [PHY, EE], "c01"),
    ("प्रकाश संश्लेषण पर नोट्स बनाइए।", [N], True, [BIO], "c02"),
    ("न्यूटन के गति के नियमों पर 8 फ्लैशकार्ड बनाइए।", [F], True, [PHY], "c03"),
    ("डीएनए की संरचना पर एक पॉडकास्ट बनाइए जिसमें दो लोग बात करें।", [P], True, [BIO], "c04"),
    ("पाइथागोरस प्रमेय पर एक एनिमेटेड वीडियो बनाइए।", [V], True, [MAT], "c05"),
    ("पाइथन में बाइनरी सर्च का कोड लिखिए।", [C], True, [CS], "c06"),
    ("अम्ल और क्षार पर एक क्विज़ बनाइए।", [Q], True, [CHE], "c07"),
    ("मेरी कल परीक्षा है, आवर्त सारणी पर मैं खुद को परखना चाहता हूँ।", [Q], False, [CHE], "c08"),
    ("विद्युत धारा क्या है?", [N], False, [PHY, EE], "c09"),
    ("जल चक्र पर नोट्स, फ्लैशकार्ड और क्विज़ बनाइए।", [N, F, Q], True, [ES], "c10"),
    ("भूकंप के बारे में एक ऑडियो पॉडकास्ट बनाइए, मुझे सुनकर सीखना पसंद है।", [P], True, [ES], "c11"),
    ("मानव हृदय की कार्यप्रणाली पर एक वीडियो और नोट्स बनाइए।", [V, N], True, [BIO], "c12"),
    ("ट्रांजिस्टर स्विच की तरह कैसे काम करता है?", [N], False, [ELN], "c13"),
    ("रासायनिक बंधन पर फ्लैशकार्ड और पॉडकास्ट बनाइए।", [F, P], True, [CHE], "c14"),
    ("किरचॉफ के नियम समझाइए और कुछ अभ्यास प्रश्न दीजिए।", [N, Q], True, [EE, PHY], "c15"),
    ("पाइथन में फिबोनाची श्रृंखला का प्रोग्राम लिखिए और उसे समझाइए।", [C, N], True, [CS, MAT], "c16"),
    ("x² का अवकलज 2x क्यों होता है, एनीमेशन से दिखाइए।", [V], True, [MAT], "c17"),
    ("कोशिका विभाजन पर पूरा पाठ: नोट्स, फ्लैशकार्ड, क्विज़, पॉडकास्ट और वीडियो।", [N, F, Q, P, V], True, [BIO], "c18"),
    ("एंजाइमों के नाम याद करने में मेरी मदद कीजिए।", [F], False, [BIO], "c19"),
    ("चार-स्ट्रोक इंजन कैसे काम करता है, इस पर एक वीडियो बनाइए।", [V], True, [ME], "c20"),
    ("ऊष्मागतिकी के नियमों पर एक क्विज़ और फ्लैशकार्ड बनाइए।", [Q, F], True, [PHY, ME, CHE], "c21"),
    ("प्रत्यावर्ती धारा और दिष्ट धारा में अंतर समझाइए।", [N], False, [EE, PHY], "c22"),
    ("ज्वालामुखी पर एक पॉडकास्ट और क्विज़ बनाइए।", [P, Q], True, [ES], "c23"),
    ("पाइथन में स्टैक का कोड लिखिए और उस पर एक क्विज़ दीजिए।", [C, Q], True, [CS], "c24"),
    ("लेंस से प्रतिबिंब कैसे बनता है, इस पर नोट्स और एनिमेटेड वीडियो बनाइए।", [N, V], True, [PHY], "c25"),
    ("प्रकाश-विद्युत प्रभाव पर संक्षिप्त नोट्स लिखिए।", [N], True, [PHY], "c26"),
    ("त्रिकोणमिति की सर्वसमिकाओं पर मेरी परीक्षा लीजिए।", [Q], False, [MAT], "c27"),
    ("कंक्रीट की मज़बूती पर एक छोटा क्विज़ बनाइए।", [Q], True, [CE], "c28"),
    ("डीसी मोटर कैसे काम करती है, इसका एनीमेशन और एक पॉडकास्ट बनाइए।", [V, P], True, [EE, PHY], "c29"),
    ("जलवायु परिवर्तन और ग्रीनहाउस प्रभाव समझाइए।", [N], False, [ES], "c30"),
    ("लॉजिक गेट्स पर फ्लैशकार्ड बनाइए।", [F], True, [ELN, CS], "c31"),
    ("नाइट्रोजन चक्र पर नोट्स, वीडियो और फ्लैशकार्ड चाहिए।", [N, V, F], True, [BIO, ES], "c32"),
    ("बर्नूली सिद्धांत को उदाहरण सहित समझाइए।", [N], False, [PHY, ME], "c33"),
    ("पाइथन में द्विघात समीकरण हल करने का प्रोग्राम लिखिए।", [C], True, [MAT], "c34"),
    ("मानव पाचन तंत्र पर फ्लैशकार्ड, क्विज़ और पॉडकास्ट बनाइए।", [F, Q, P], True, [BIO], "c35"),
    ("संधारित्र के आवेशित और अनावेशित होने पर एक वीडियो बनाइए और कुछ प्रश्न पूछिए।", [V, Q], True, [ELN, EE, PHY], "c36"),
    ("ओम के नियम पर पूरा पाठ बनाइए: नोट्स, फ्लैशकार्ड, क्विज़, पॉडकास्ट, वीडियो और कोड।", ALL6, True, [PHY, EE], "c37"),
    ("रेडियोधर्मी क्षय का पाइथन सिमुलेशन कोड लिखिए।", [C], True, [PHY], "c38"),
    ("सौरमंडल के ग्रहों पर एक पॉडकास्ट बनाइए।", [P], True, [PHY, ES, "other"], "c39"),
    ("मृदा यांत्रिकी की मौखिक परीक्षा के लिए मुझे अभ्यास करवाइए।", [Q], False, [CE], "c40"),
]

HINGLISH = [
    ("Ohm's law samjhao aur ek chhota quiz bhi do.", [N, Q], True, [PHY, EE], "c01"),
    ("Photosynthesis pe notes bana do please.", [N], True, [BIO], "c02"),
    ("Newton ke laws of motion pe flashcards bana do.", [F], True, [PHY], "c03"),
    ("Mujhe binary search ka Python code chahiye.", [C], True, [CS], "c06"),
    ("DNA ki structure pe ek podcast banao jisme do log baat karein.", [P], True, [BIO], "c04"),
    ("Pythagoras theorem ka animated video bana do.", [V], True, [MAT], "c05"),
    ("Kal mera chemistry ka exam hai, acids and bases pe mujhe test karo.", [Q], False, [CHE], "c44"),
    ("Transistor switch ki tarah kaise kaam karta hai?", [N], False, [ELN], "c13"),
    ("Water cycle pe notes, flashcards aur quiz chahiye.", [N, F, Q], True, [ES], "c10"),
    ("Travel karte waqt sunna pasand hai, plate tectonics pe kuch audio bana do.", [P], True, [ES], "c43"),
    ("x square ka derivative 2x kyun hota hai, animation se dikhao.", [V], True, [MAT], "c17"),
    ("Recursion Python mein samjhao, code examples ke saath.", [N, C], True, [CS], "c41"),
    ("Enzymes ke naam yaad nahi rehte, yaad karne mein help karo.", [F], False, [BIO], "c19"),
    ("Four stroke engine kaise kaam karta hai, video aur notes dono do.", [V, N], True, [ME], "c20b"),
    ("Thermodynamics ke laws pe quiz aur flashcards banao.", [Q, F], True, [PHY, ME, CHE], "c21"),
    ("AC aur DC current mein kya difference hai?", [N], False, [EE, PHY], "c22"),
    ("Volcanoes pe ek podcast aur quiz bana do.", [P, Q], True, [ES], "c23"),
    ("Stack aur queue ka Python code likho aur unpe notes bhi do.", [C, N], True, [CS], "c42"),
    ("Cell division pe poora lesson chahiye - notes, flashcards, quiz, podcast, video sab.", [N, F, Q, P, V], True, [BIO], "c18"),
    ("Logic gates ke truth tables yaad karne ke liye flashcards bana do.", [F], True, [ELN, CS], "c31"),
    ("Trigonometry identities pe meri practice karwao.", [Q], False, [MAT], "c27"),
    ("Capacitor charge aur discharge ka animation aur kuch MCQs do.", [V, Q], True, [ELN, EE, PHY], "c36"),
    ("Quadratic equation solve karne ka Python program likho.", [C], True, [MAT], "c34"),
    ("Climate change aur greenhouse effect samjhao.", [N], False, [ES], "c30"),
    ("Soil mechanics viva ki taiyari karni hai, practice questions do.", [Q], True, [CE], "c40"),
]

KN = [
    ("ಓಮ್‌ನ ನಿಯಮವನ್ನು ಒಂದು ಸಣ್ಣ ರಸಪ್ರಶ್ನೆಯೊಂದಿಗೆ ವಿವರಿಸಿ.", [N, Q], True, [PHY, EE], "c01"),
    ("ದ್ಯುತಿಸಂಶ್ಲೇಷಣೆಯ ಬಗ್ಗೆ ಟಿಪ್ಪಣಿಗಳನ್ನು ಮಾಡಿ.", [N], True, [BIO], "c02"),
    ("ನ್ಯೂಟನ್‌ನ ಚಲನೆಯ ನಿಯಮಗಳ ಬಗ್ಗೆ ಫ್ಲ್ಯಾಶ್‌ಕಾರ್ಡ್‌ಗಳನ್ನು ಮಾಡಿ.", [F], True, [PHY], "c03"),
    ("ಡಿಎನ್‌ಎ ರಚನೆಯ ಬಗ್ಗೆ ಇಬ್ಬರು ಮಾತನಾಡುವ ಒಂದು ಪಾಡ್‌ಕಾಸ್ಟ್ ಮಾಡಿ.", [P], True, [BIO], "c04"),
    ("ಪೈಥಾಗರಸ್ ಪ್ರಮೇಯದ ಬಗ್ಗೆ ಒಂದು ಅನಿಮೇಟೆಡ್ ವೀಡಿಯೊ ಮಾಡಿ.", [V], True, [MAT], "c05"),
    ("ಪೈಥಾನ್‌ನಲ್ಲಿ ಬೈನರಿ ಸರ್ಚ್‌ಗೆ ಕೋಡ್ ಬರೆಯಿರಿ.", [C], True, [CS], "c06"),
    ("ಆಮ್ಲಗಳು ಮತ್ತು ಪ್ರತ್ಯಾಮ್ಲಗಳ ಬಗ್ಗೆ ಒಂದು ರಸಪ್ರಶ್ನೆ ಮಾಡಿ.", [Q], True, [CHE], "c07"),
    ("ನಾಳೆ ನನಗೆ ಆವರ್ತಕ ಕೋಷ್ಟಕದ ಪರೀಕ್ಷೆ ಇದೆ, ನನ್ನನ್ನು ಪರೀಕ್ಷಿಸಿ.", [Q], False, [CHE], "c08"),
    ("ವಿದ್ಯುತ್ ಪ್ರವಾಹ ಎಂದರೇನು?", [N], False, [PHY, EE], "c09"),
    ("ಜಲಚಕ್ರದ ಬಗ್ಗೆ ಟಿಪ್ಪಣಿಗಳು, ಫ್ಲ್ಯಾಶ್‌ಕಾರ್ಡ್‌ಗಳು ಮತ್ತು ರಸಪ್ರಶ್ನೆ ಮಾಡಿ.", [N, F, Q], True, [ES], "c10"),
    ("ಭೂಕಂಪಗಳ ಬಗ್ಗೆ ಒಂದು ಆಡಿಯೊ ಪಾಡ್‌ಕಾಸ್ಟ್ ಮಾಡಿ, ನನಗೆ ಕೇಳಿ ಕಲಿಯುವುದು ಇಷ್ಟ.", [P], True, [ES], "c11"),
    ("ಮಾನವ ಹೃದಯದ ಕಾರ್ಯದ ಬಗ್ಗೆ ಒಂದು ವೀಡಿಯೊ ಮತ್ತು ಟಿಪ್ಪಣಿಗಳನ್ನು ಮಾಡಿ.", [V, N], True, [BIO], "c12"),
    ("ಟ್ರಾನ್ಸಿಸ್ಟರ್ ಸ್ವಿಚ್‌ನಂತೆ ಹೇಗೆ ಕೆಲಸ ಮಾಡುತ್ತದೆ?", [N], False, [ELN], "c13"),
    ("ರಾಸಾಯನಿಕ ಬಂಧಗಳ ಬಗ್ಗೆ ಫ್ಲ್ಯಾಶ್‌ಕಾರ್ಡ್‌ಗಳು ಮತ್ತು ಪಾಡ್‌ಕಾಸ್ಟ್ ಮಾಡಿ.", [F, P], True, [CHE], "c14"),
    ("ಕಿರ್ಚಾಫ್‌ನ ನಿಯಮಗಳನ್ನು ವಿವರಿಸಿ ಮತ್ತು ಕೆಲವು ಅಭ್ಯಾಸ ಪ್ರಶ್ನೆಗಳನ್ನು ಕೊಡಿ.", [N, Q], True, [EE, PHY], "c15"),
    ("ಪೈಥಾನ್‌ನಲ್ಲಿ ಫಿಬೊನಾಚಿ ಸರಣಿಯ ಪ್ರೋಗ್ರಾಂ ಬರೆದು ಅದನ್ನು ವಿವರಿಸಿ.", [C, N], True, [CS, MAT], "c16"),
    ("x² ನ ಅವಕಲಜ 2x ಏಕೆ ಎಂದು ಅನಿಮೇಷನ್ ಮೂಲಕ ತೋರಿಸಿ.", [V], True, [MAT], "c17"),
    ("ಕೋಶ ವಿಭಜನೆಯ ಬಗ್ಗೆ ಸಂಪೂರ್ಣ ಪಾಠ: ಟಿಪ್ಪಣಿಗಳು, ಫ್ಲ್ಯಾಶ್‌ಕಾರ್ಡ್‌ಗಳು, ರಸಪ್ರಶ್ನೆ, ಪಾಡ್‌ಕಾಸ್ಟ್ ಮತ್ತು ವೀಡಿಯೊ.", [N, F, Q, P, V], True, [BIO], "c18"),
    ("ಕಿಣ್ವಗಳ ಹೆಸರುಗಳನ್ನು ನೆನಪಿಟ್ಟುಕೊಳ್ಳಲು ನನಗೆ ಸಹಾಯ ಮಾಡಿ.", [F], False, [BIO], "c19"),
    ("ನಾಲ್ಕು-ಸ್ಟ್ರೋಕ್ ಎಂಜಿನ್ ಹೇಗೆ ಕೆಲಸ ಮಾಡುತ್ತದೆ ಎಂಬುದರ ಬಗ್ಗೆ ಒಂದು ವೀಡಿಯೊ ಮಾಡಿ.", [V], True, [ME], "c20"),
    ("ಉಷ್ಣಗತಿಶಾಸ್ತ್ರದ ನಿಯಮಗಳ ಬಗ್ಗೆ ರಸಪ್ರಶ್ನೆ ಮತ್ತು ಫ್ಲ್ಯಾಶ್‌ಕಾರ್ಡ್‌ಗಳನ್ನು ಮಾಡಿ.", [Q, F], True, [PHY, ME, CHE], "c21"),
    ("ಎಸಿ ಮತ್ತು ಡಿಸಿ ವಿದ್ಯುತ್ ನಡುವಿನ ವ್ಯತ್ಯಾಸವನ್ನು ವಿವರಿಸಿ.", [N], False, [EE, PHY], "c22"),
    ("ಜ್ವಾಲಾಮುಖಿಗಳ ಬಗ್ಗೆ ಒಂದು ಪಾಡ್‌ಕಾಸ್ಟ್ ಮತ್ತು ರಸಪ್ರಶ್ನೆ ಮಾಡಿ.", [P, Q], True, [ES], "c23"),
    ("ಪೈಥಾನ್‌ನಲ್ಲಿ ಸ್ಟ್ಯಾಕ್‌ನ ಕೋಡ್ ಬರೆಯಿರಿ ಮತ್ತು ಅದರ ಬಗ್ಗೆ ಒಂದು ರಸಪ್ರಶ್ನೆ ಕೊಡಿ.", [C, Q], True, [CS], "c24"),
    ("ಮಸೂರವು ಪ್ರತಿಬಿಂಬವನ್ನು ಹೇಗೆ ರೂಪಿಸುತ್ತದೆ ಎಂಬುದರ ಬಗ್ಗೆ ಟಿಪ್ಪಣಿಗಳು ಮತ್ತು ಅನಿಮೇಟೆಡ್ ವೀಡಿಯೊ ಮಾಡಿ.", [N, V], True, [PHY], "c25"),
    ("ದ್ಯುತಿವಿದ್ಯುತ್ ಪರಿಣಾಮದ ಬಗ್ಗೆ ಸಂಕ್ಷಿಪ್ತ ಟಿಪ್ಪಣಿಗಳನ್ನು ಬರೆಯಿರಿ.", [N], True, [PHY], "c26"),
    ("ತ್ರಿಕೋನಮಿತಿಯ ಸರ್ವಸಮತೆಗಳ ಬಗ್ಗೆ ನನ್ನನ್ನು ಪರೀಕ್ಷಿಸಿ.", [Q], False, [MAT], "c27"),
    ("ಕಾಂಕ್ರೀಟ್‌ನ ಬಲದ ಬಗ್ಗೆ ಒಂದು ಸಣ್ಣ ರಸಪ್ರಶ್ನೆ ಮಾಡಿ.", [Q], True, [CE], "c28"),
    ("ಡಿಸಿ ಮೋಟಾರ್ ಹೇಗೆ ಕೆಲಸ ಮಾಡುತ್ತದೆ ಎಂಬುದರ ಅನಿಮೇಷನ್ ಮತ್ತು ಒಂದು ಪಾಡ್‌ಕಾಸ್ಟ್ ಮಾಡಿ.", [V, P], True, [EE, PHY], "c29"),
    ("ಹವಾಮಾನ ಬದಲಾವಣೆ ಮತ್ತು ಹಸಿರುಮನೆ ಪರಿಣಾಮವನ್ನು ವಿವರಿಸಿ.", [N], False, [ES], "c30"),
    ("ಲಾಜಿಕ್ ಗೇಟ್‌ಗಳ ಬಗ್ಗೆ ಫ್ಲ್ಯಾಶ್‌ಕಾರ್ಡ್‌ಗಳನ್ನು ಮಾಡಿ.", [F], True, [ELN, CS], "c31"),
    ("ಸಾರಜನಕ ಚಕ್ರದ ಬಗ್ಗೆ ಟಿಪ್ಪಣಿಗಳು, ವೀಡಿಯೊ ಮತ್ತು ಫ್ಲ್ಯಾಶ್‌ಕಾರ್ಡ್‌ಗಳು ಬೇಕು.", [N, V, F], True, [BIO, ES], "c32"),
    ("ಬರ್ನೌಲಿ ತತ್ವವನ್ನು ಉದಾಹರಣೆಗಳೊಂದಿಗೆ ವಿವರಿಸಿ.", [N], False, [PHY, ME], "c33"),
    ("ಪೈಥಾನ್‌ನಲ್ಲಿ ವರ್ಗ ಸಮೀಕರಣವನ್ನು ಬಿಡಿಸುವ ಪ್ರೋಗ್ರಾಂ ಬರೆಯಿರಿ.", [C], True, [MAT], "c34"),
    ("ಮಾನವ ಜೀರ್ಣಾಂಗ ವ್ಯವಸ್ಥೆಯ ಬಗ್ಗೆ ಫ್ಲ್ಯಾಶ್‌ಕಾರ್ಡ್‌ಗಳು, ರಸಪ್ರಶ್ನೆ ಮತ್ತು ಪಾಡ್‌ಕಾಸ್ಟ್ ಮಾಡಿ.", [F, Q, P], True, [BIO], "c35"),
    ("ಕೆಪಾಸಿಟರ್ ಚಾರ್ಜ್ ಮತ್ತು ಡಿಸ್ಚಾರ್ಜ್ ಆಗುವ ಬಗ್ಗೆ ಒಂದು ವೀಡಿಯೊ ಮಾಡಿ ಮತ್ತು ಕೆಲವು ಪ್ರಶ್ನೆಗಳನ್ನು ಕೇಳಿ.", [V, Q], True, [ELN, EE, PHY], "c36"),
    ("ಓಮ್‌ನ ನಿಯಮದ ಬಗ್ಗೆ ಸಂಪೂರ್ಣ ಪಾಠ ಮಾಡಿ: ಟಿಪ್ಪಣಿಗಳು, ಫ್ಲ್ಯಾಶ್‌ಕಾರ್ಡ್‌ಗಳು, ರಸಪ್ರಶ್ನೆ, ಪಾಡ್‌ಕಾಸ್ಟ್, ವೀಡಿಯೊ ಮತ್ತು ಕೋಡ್.", ALL6, True, [PHY, EE], "c37"),
    ("ವಿಕಿರಣಶೀಲ ಕ್ಷಯದ ಪೈಥಾನ್ ಸಿಮ್ಯುಲೇಶನ್ ಕೋಡ್ ಬರೆಯಿರಿ.", [C], True, [PHY], "c38"),
    ("ಸೌರವ್ಯೂಹದ ಗ್ರಹಗಳ ಬಗ್ಗೆ ಒಂದು ಪಾಡ್‌ಕಾಸ್ಟ್ ಮಾಡಿ.", [P], True, [PHY, ES, "other"], "c39"),
    ("ಮಣ್ಣಿನ ಯಂತ್ರಶಾಸ್ತ್ರದ ಮೌಖಿಕ ಪರೀಕ್ಷೆಗೆ ನನಗೆ ಅಭ್ಯಾಸ ಮಾಡಿಸಿ.", [Q], False, [CE], "c40"),
]

# out of scope: errands, entertainment, non-technical learning ("hard negatives")
OOS = [
    ("en", "Book me a train ticket from Lucknow to Delhi for tomorrow.", False),
    ("en", "Write a romantic poem for my wife's birthday.", False),
    ("en", "What's the weather like in Bengaluru today?", False),
    ("en", "Recommend a good movie to watch tonight.", False),
    ("en", "Tell me a joke.", False),
    ("en", "Order a pizza with extra cheese.", False),
    ("en", "Who won yesterday's cricket match?", False),
    ("en", "Help me write a leave application to my manager.", False),
    ("en", "Set an alarm for 6 am.", False),
    ("en", "What's the best smartphone under 20,000 rupees?", False),
    ("en", "Plan a three-day trip to Goa.", False),
    ("en", "Summarise the causes of the French Revolution for my history exam.", True),
    ("en", "Teach me the Spanish past tense with flashcards.", True),
    ("hi", "कल के लिए दिल्ली की ट्रेन टिकट बुक कर दो।", False),
    ("hi", "आज मौसम कैसा रहेगा?", False),
    ("hi", "मेरे दोस्त के जन्मदिन के लिए एक कविता लिखो।", False),
    ("hi", "कोई अच्छी फ़िल्म बताओ।", False),
    ("hinglish", "Aaj ka cricket score kya hai?", False),
    ("hinglish", "Mere liye ek pizza order kar do.", False),
    ("hinglish", "Koi achhi web series suggest karo.", False),
    ("kn", "ನಾಳೆ ಮೈಸೂರಿಗೆ ಬಸ್ ಟಿಕೆಟ್ ಬುಕ್ ಮಾಡಿ.", False),
    ("kn", "ಇಂದು ಹವಾಮಾನ ಹೇಗಿದೆ?", False),
    ("kn", "ಒಂದು ಒಳ್ಳೆಯ ಕನ್ನಡ ಸಿನಿಮಾ ಸೂಚಿಸಿ.", False),
    ("kn", "ನನ್ನ ಸ್ನೇಹಿತನ ಹುಟ್ಟುಹಬ್ಬಕ್ಕೆ ಒಂದು ಕವನ ಬರೆಯಿರಿ.", False),
    ("kn", "ಒಂದು ಜೋಕ್ ಹೇಳಿ.", False),
    ("kn", "ಬೆಳಿಗ್ಗೆ ಆರು ಗಂಟೆಗೆ ಅಲಾರಂ ಇಡಿ.", False),
]

# end-to-end set (P4): 1-based indices per slice - mixes 1/2/3/5/6-artefact asks, explicit and implicit,
# and the same concepts in Hindi and Kannada (c01 c02 c04 c07 c09 c10 c12 c16 c19 c23 c27 c32 c34 c37)
E2E = {
    "en": [1, 3, 4, 5, 6, 7, 8, 10, 13, 17, 19, 25, 30, 40, 43, 47, 52, 53, 58, 66],
    "hi": [1, 2, 4, 7, 9, 10, 12, 16, 19, 23, 27, 32, 34, 37],
    "hinglish": [1, 3, 4, 7, 9, 14, 17, 24],
    "kn": [1, 2, 4, 7, 9, 10, 12, 16, 19, 23, 27, 32, 34, 37],
    "oos": [1, 2, 14, 21],
}
ABLATION = {"en": 25, "hi": 15, "hinglish": 10, "kn": 15, "oos": 10}
SEED = 2026


def main() -> None:
    rows: list[dict] = []
    for slice_, lang, items in (("en", "en", EN), ("hi", "hi", HI), ("hinglish", "hi", HINGLISH), ("kn", "kn", KN)):
        for i, (text, gold, explicit, subjects, pair) in enumerate(items, 1):
            rows.append({"id": f"{slice_}-{i:02d}", "slice": slice_, "lang": lang, "text": text,
                         "gold": [g for g in ALL6 if g in gold], "explicit": explicit, "subject": subjects,
                         "in_scope": True, "pair": pair, "hard_negative": False,
                         "e2e": i in E2E[slice_], "ablation": False})
    for i, (lang, text, hard) in enumerate(OOS, 1):
        rows.append({"id": f"oos-{i:02d}", "slice": "oos", "lang": "hi" if lang == "hinglish" else lang,
                     "oos_lang": lang, "text": text, "gold": [], "explicit": True, "subject": ["other"],
                     "in_scope": False, "pair": None, "hard_negative": hard,
                     "e2e": i in E2E["oos"], "ablation": False})
    rng = random.Random(SEED)
    for slice_, n in ABLATION.items():
        ids = [r["id"] for r in rows if r["slice"] == slice_]
        chosen = set(rng.sample(ids, n))
        for r in rows:
            if r["id"] in chosen:
                r["ablation"] = True
    assert len({r["id"] for r in rows}) == len(rows)
    for r in rows:
        assert set(r["gold"]) <= set(ALL6), r
        assert r["in_scope"] == bool(r["gold"]), r
    out = Path(__file__).with_name("requests.jsonl")
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    by = {}
    for r in rows:
        by.setdefault(r["slice"], [0, 0, 0])
        by[r["slice"]][0] += 1
        by[r["slice"]][1] += r["e2e"]
        by[r["slice"]][2] += r["ablation"]
    print(f"{len(rows)} requests -> {out}")
    for s, (n, e, a) in by.items():
        print(f"  {s:<9} {n:3d} total, {e:2d} end-to-end, {a:2d} ablation")


if __name__ == "__main__":
    main()
