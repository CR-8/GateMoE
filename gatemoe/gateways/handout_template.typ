// GateMoE printable handout. All lesson content arrives as JSON in sys.inputs.data and is
// inserted as plain text (strings are never evaluated as Typst markup or code).
#let d = json(bytes(sys.inputs.at("data")))
#let accent = rgb("#2563eb")
#set document(title: d.title)
#set page(paper: "a4", margin: (x: 17mm, y: 16mm), numbering: "1 / 1",
  footer: context [#set text(size: 7.5pt, fill: gray)
    #d.footer #h(1fr) #counter(page).display("1 / 1", both: true)])
#set text(font: d.fonts, size: 10.5pt, lang: d.lang)
#set par(leading: 0.62em, spacing: 0.9em)
#show heading.where(level: 1): it => block(below: 0.6em, text(size: 18pt, weight: "bold", fill: accent, it.body))
#show heading.where(level: 2): it => block(above: 1.1em, below: 0.5em, text(size: 12.5pt, weight: "bold", it.body))

= #d.title
#if d.subtitle != "" [#text(fill: gray, d.subtitle)]
#line(length: 100%, stroke: 0.6pt + accent)

#for s in d.sections [
  == #s.heading
  #for blk in s.blocks {
    if blk.kind == "list" { list(..blk.items.map(i => [#i])) } else { par[#blk.text] }
  }
]

#if d.key_points.len() > 0 [
  == #d.labels.key_points
  #block(fill: rgb("#eef2ff"), inset: 9pt, radius: 4pt, width: 100%,
    list(..d.key_points.map(k => [#k])))
]

#if d.concept_map != none [
  == #d.labels.concept_map
  // Graphviz SVG produced by GateMoE itself (labels escaped there); Typst draws its text with the handout fonts
  #align(center, image(bytes(d.concept_map), format: "svg", width: d.concept_map_w * 1pt))
]

#if d.glossary.len() > 0 [
  == #d.labels.glossary
  #table(columns: (auto, 1fr), stroke: 0.4pt + luma(200), inset: 6pt,
    ..d.glossary.map(g => ([*#g.term*], [#g.definition])).flatten())
]

#if d.flashcards.len() > 0 [
  == #d.labels.flashcards
  #text(size: 8.5pt, fill: gray, d.labels.cut_hint)
  #table(columns: (1fr, 1fr), stroke: (dash: "dashed", paint: luma(160), thickness: 0.5pt), inset: 8pt,
    ..d.flashcards.map(c => ([*#c.front*], [#c.back])).flatten())
]

#if d.quiz.len() > 0 [
  == #d.labels.quiz
  #for (n, q) in d.quiz.enumerate() [
    #block(breakable: false, below: 0.9em)[
      *#(n + 1).* #q.question \
      #for (j, o) in q.options.enumerate() [
        #h(1.2em) #("abcd".at(j)))  #o \
      ]
    ]
  ]
  #pagebreak()
  == #d.labels.answers
  #for (n, q) in d.quiz.enumerate() [
    *#(n + 1).* #("abcd".at(q.answer_index)) — #q.explanation \
  ]
]

#if d.simulations.len() > 0 [
  == #d.labels.simulations
  #text(size: 8.5pt, fill: gray, d.labels.sim_hint)
  #for s in d.simulations [- #s \ ]
]

#if d.sources.len() > 0 [
  == #d.labels.sources
  #set text(size: 8.5pt)
  #for s in d.sources [- #s \ ]
]
