"""Number verbalisation for engines that cannot read digits (Meta MMS character models).

MMS vocabularies have no (or only some) digits - e.g. mms-tts-guj has none,
mms-tts-tam lacks '8' - so "2024" would be silently dropped or garbled.
verbalize_numbers(text, lang) rewrites numbers as words:
  * num2words (LGPL, pure Python, optional) when it supports the language
    (Indic coverage in 0.5.14: kn, te, bn; also en, hi is NOT covered);
  * otherwise digit-by-digit with native digit names ("2024" -> "two zero two four"),
    '.' between digits -> "point", '%' -> "percent".
Native-script digits (०-९, ௦-௯, ૦-૯, ...) are mapped to ASCII first.
Best practice is still to make the LLM write numbers as words for podcast scripts.
"""
from __future__ import annotations

import re

# 0..9, point, percent
_DIGITS = {
    "kn": ("ಸೊನ್ನೆ ಒಂದು ಎರಡು ಮೂರು ನಾಲ್ಕು ಐದು ಆರು ಏಳು ಎಂಟು ಒಂಬತ್ತು", "ಬಿಂದು", "ಶೇಕಡಾ"),
    "ta": ("பூஜ்ஜியம் ஒன்று இரண்டு மூன்று நான்கு ஐந்து ஆறு ஏழு எட்டு ஒன்பது", "புள்ளி", "சதவீதம்"),
    "te": ("సున్నా ఒకటి రెండు మూడు నాలుగు ఐదు ఆరు ఏడు ఎనిమిది తొమ్మిది", "బిందువు", "శాతం"),
    "ml": ("പൂജ്യം ഒന്ന് രണ്ട് മൂന്ന് നാല് അഞ്ച് ആറ് ഏഴ് എട്ട് ഒമ്പത്", "ദശാംശം", "ശതമാനം"),
    "gu": ("શૂન્ય એક બે ત્રણ ચાર પાંચ છ સાત આઠ નવ", "દશાંશ", "ટકા"),
    "pa": ("ਸਿਫ਼ਰ ਇੱਕ ਦੋ ਤਿੰਨ ਚਾਰ ਪੰਜ ਛੇ ਸੱਤ ਅੱਠ ਨੌਂ", "ਦਸ਼ਮਲਵ", "ਪ੍ਰਤੀਸ਼ਤ"),
    "mr": ("शून्य एक दोन तीन चार पाच सहा सात आठ नऊ", "दशांश", "टक्के"),
    "hi": ("शून्य एक दो तीन चार पाँच छह सात आठ नौ", "दशमलव", "प्रतिशत"),
    "bn": ("শূন্য এক দুই তিন চার পাঁচ ছয় সাত আট নয়", "দশমিক", "শতাংশ"),
    "as": ("শূন্য এক দুই তিনি চাৰি পাঁচ ছয় সাত আঠ ন", "দশমিক", "শতাংশ"),
    "or": ("ଶୂନ ଏକ ଦୁଇ ତିନି ଚାରି ପାଞ୍ଚ ଛଅ ସାତ ଆଠ ନଅ", "ଦଶମିକ", "ପ୍ରତିଶତ"),
}
# Unicode decimal digit blocks of Indic scripts (zero code point)
_ZEROS = [0x0966, 0x09E6, 0x0A66, 0x0AE6, 0x0B66, 0x0BE6, 0x0C66, 0x0CE6, 0x0D66]
_TO_ASCII = {chr(z + i): str(i) for z in _ZEROS for i in range(10)}
_NUM = re.compile(r"(?<![\w.])(\d+(?:[.,]\d+)*)(\s*%)?")


def _num2words(n: str, lang: str):
    try:
        from num2words import num2words
    except ImportError:
        return None
    try:
        v = float(n) if "." in n else int(n)
        return num2words(v, lang=lang)
    except (NotImplementedError, ValueError, OverflowError, KeyError):
        return None


def verbalize_numbers(text: str, lang: str) -> str:
    text = "".join(_TO_ASCII.get(c, c) for c in text)
    if lang not in _DIGITS:
        return text
    names, point, percent = _DIGITS[lang]
    names = names.split()

    def rep(m: re.Match) -> str:
        num = m.group(1).replace(",", "")          # 1,000 -> 1000 (Indian 1,00,000 too)
        words = _num2words(num, lang) if len(num) <= 15 else None
        if words is None:
            parts = []
            for ch in num:
                parts.append(point if ch == "." else names[int(ch)])
            words = " ".join(parts)
        if m.group(2):
            words += " " + percent
        return " " + words + " "

    return re.sub(r"\s+", " ", _NUM.sub(rep, text)).strip()


if __name__ == "__main__":
    for l, s in [("kn", "ಸನ್ 2024ರಲ್ಲಿ 3.5% ಏರಿಕೆ"), ("ta", "2024 இல் 8 கிரகங்கள், 45%"),
                 ("gu", "૨૦૨૪ માં 9.8 મીટર"), ("pa", "100 ਡਿਗਰੀ"), ("hi", "सन् २०२४ में 3 वैज्ञानिक")]:
        print(l, "->", verbalize_numbers(s, l))
