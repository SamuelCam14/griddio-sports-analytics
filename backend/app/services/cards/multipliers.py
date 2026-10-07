"""
Tablas paramétricas de multiplicadores para valoración hedónica de Sports Cards.
"""

UNIFORM_MULTIPLIERS = {
    "Rookie Card": 1.00,        # Uniforme oficial NBA (Panini Prizm, Optic, Select)
    "Veteran": 0.85,            # Uniforme NBA veterano
    "NIL": 0.48,                # Descuento Efecto Uniforme colegial NCAA (Bowman U, Leaf)
    "Draft Picks": 0.52,        # Panini Draft Picks con uniforme universitario
}

GRADE_MULTIPLIERS = {
    "Raw": 1.00,
    "PSA 8": 0.95,
    "PSA 9": 1.65,
    "BGS 9.5": 2.40,
    "PSA 10": 3.85,
    "SGC 10": 2.80,
    "BGS 10 Black Label": 12.00,
}

PARALLEL_MULTIPLIERS = {
    "Base": 1.00,
    "Silver": 2.20,
    "Prizm": 2.20,
    "Refractor": 2.10,
    "Red /299": 3.20,
    "Blue /199": 3.80,
    "Purple /99": 5.50,
    "Orange /49": 7.50,
    "Tie-Dye /25": 9.50,
    "Mojo /25": 9.20,
    "Gold /10": 18.00,
    "Green /5": 28.00,
    "1/1 Nebula": 65.00,
    "1/1 Black Prizm": 75.00,
}

AUTOGRAPH_MULTIPLIERS = {
    "None": 1.00,
    "Sticker": 2.30,
    "On-Card": 4.10,
}

PLAYER_BASE_PEDIGREE = {
    "Victor Wembanyama": 65.0,
    "Cooper Flagg": 50.0,
    "Chet Holmgren": 30.0,
    "Luka Doncic": 45.0,
    "Paolo Banchero": 25.0,
    "Stephon Castle": 22.0,
    "Default Rookie": 15.0,
}
