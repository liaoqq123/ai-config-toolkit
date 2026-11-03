"""Create fictional workbooks without overwriting existing files."""
from pathlib import Path
import argparse
import json
from openpyxl import Workbook


def create(output):
    output = Path(output).resolve()
    names = ("Catalog.xlsx", "Groups.xlsx")
    if any((output / name).exists() for name in names):
        raise ValueError("演示工作簿已存在；请选择新的输出目录，避免覆盖已有编辑。")
    output.mkdir(parents=True, exist_ok=True)
    def workbook(name, logical, fields, types, constraints, records):
        book = Workbook()
        sheet = book.active
        sheet.title = "配置"
        sheet['A1'] = logical
        for column, (field, kind, constraint) in enumerate(zip(fields, types, constraints), 2):
            sheet.cell(2, column, field + "说明")
            sheet.cell(3, column, "CS")
            sheet.cell(4, column, kind)
            sheet.cell(5, column, field)
            if constraint:
                sheet.cell(6, column, constraint)
        for row, record in enumerate(records, 7):
            for column, value in enumerate(record, 2):
                sheet.cell(row, column, value)
        sheet['A7'] = 'Start'
        sheet.cell(6 + len(records), 1, 'End')
        sheet.freeze_panes = 'C7'
        sheet.column_dimensions['B'].width = 20
        sheet.column_dimensions['C'].width = 24
        book.save(output / name)
    workbook('Groups.xlsx', 'Groups', ['Id', 'Name'], ['string', 'string'], ['PRIMARY', ''],
             [('group_a', 'Group A'), ('group_b', 'Group B')])
    workbook('Catalog.xlsx', 'Catalog', ['Id', 'Name', 'GroupId', 'Count', 'Rule'],
             ['string', 'string', 'string', 'int', 'string'], ['PRIMARY', '', 'FOREIGN:Groups.Id', '', ''],
             [('item_alpha', 'Alpha', 'group_a', 2, '=E7*2'), ('item_beta', 'Beta', 'group_b', 3, '=E8*2')])
    return {'status': 'created', 'directory': str(output), 'files': list(names), 'fictional_data_only': True}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default=str(Path(__file__).resolve().parent / 'data'))
    args = parser.parse_args()
    try:
        print(json.dumps(create(args.output), ensure_ascii=False, indent=2))
    except (OSError, ValueError) as error:
        parser.exit(2, str(error) + '\n')
