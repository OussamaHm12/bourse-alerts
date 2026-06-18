from moroccan_stock_intelligence.scrapers.bmce import BMCECapitalScraper
from moroccan_stock_intelligence.scrapers.casablanca import CasablancaBourseScraper
from moroccan_stock_intelligence.utils import parse_number


def test_parse_moroccan_numbers():
    assert parse_number("1 234,50") == 1234.50
    assert parse_number("2,54 %") == 2.54
    assert parse_number("1,282 M") == 1282000.0
    assert parse_number("-") is None


def test_casablanca_table_parser_extracts_stock_row():
    html = """
    <h3>Bâtiment et Matériaux de Construction</h3>
    <table>
      <thead><tr>
        <th>Instrument</th><th>Statut</th><th>Cours de référence</th><th>Ouverture</th>
        <th>Dernier cours</th><th>Quantité échangée</th><th>Volume</th>
        <th>Variation en %</th><th>+ haut jour</th><th>+ bas jour</th>
        <th>Meilleur prix à l'achat</th><th>Meilleur prix à la vente</th>
        <th>Quantité Meilleur prix à l'achat</th><th>Quantité Meilleur prix à la vente</th>
        <th>Capitalisation</th><th>Nombre de transactions</th>
      </tr></thead>
      <tbody><tr>
        <td><a href="/fr/live-market/instruments/TGC">TGCC</a></td><td>T</td>
        <td>780,00</td><td>781,00</td><td>782,50</td><td>12 345</td>
        <td>9 654 321,10</td><td>0,32 %</td><td>790,00</td><td>775,00</td>
        <td>781,00</td><td>783,00</td><td>100</td><td>200</td>
        <td>25 000 000 000,00</td><td>42</td>
      </tr></tbody>
    </table>
    """
    rows = CasablancaBourseScraper().parse(html)
    assert len(rows) == 1
    row = rows[0]
    assert row.symbol == "TGC"
    assert row.company_name == "TGCC"
    assert row.sector == "Bâtiment et Matériaux de Construction"
    assert row.current_price == 782.50
    assert row.daily_variation == 0.32
    assert row.volume == 9654321.10
    assert row.market_cap == 25000000000.00


def test_bmce_parser_ignores_aggregate_rows():
    html = """
    <table>
      <tr>
        <td>Valeur Cours Variation % Quantité Zellidja 215,00 +4,88% 53 Addoha 36,70 +4,86% 1,282 M</td>
        <td><a href="/bkbbourse/details/783273%2C102%2C608">Zellidja</a></td>
        <td>215,00</td><td>+4,88%</td><td>53</td>
      </tr>
      <tr>
        <td><a href="/bkbbourse/details/783273%2C102%2C608">Zellidja</a></td>
        <td>215,00</td><td>+4,88%</td><td>53</td>
      </tr>
      <tr>
        <td><a href="/bkbbourse/details/2585582%2C102%2C608">Addoha</a></td>
        <td>36,70</td><td>+4,86%</td><td>1,282 M</td>
      </tr>
    </table>
    """
    rows = BMCECapitalScraper().parse(html)
    assert [row.company_name for row in rows] == ["Zellidja", "Addoha"]
    assert rows[1].volume == 1282000.0
