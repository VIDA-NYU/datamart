import csv
import xlrd
import xlrd.sheet

from .utils import SimpleConverter


def xls_to_csv(source_filename, dest_fileobj):
    workbook = xlrd.open_workbook(source_filename)
    datemode = workbook.datemode
    sheets = workbook.sheets()
    if len(sheets) != 1:
        raise ValueError("Excel workbook has %d sheets" % len(sheets))
    sheet, = sheets

    writer = csv.writer(dest_fileobj)
    for row_num in range(sheet.nrows):
        values = sheet.row_values(row_num)

        # Decode dates into ISO-8601 strings
        for col_num, cell_type in enumerate(sheet.row_types(row_num)):
            if cell_type == xlrd.sheet.XL_CELL_DATE:
                values[col_num] = xlrd.xldate_as_datetime(
                    values[col_num],
                    datemode,
                ).isoformat()

        writer.writerow(values)


class ExcelConverter(SimpleConverter):
    """Adapter converting Excel files to CSV.
    """
    transform = staticmethod(xls_to_csv)
