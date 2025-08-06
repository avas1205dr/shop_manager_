import sqlite3

connection = sqlite3.connect('identifier.sqlite')
curs = connection.cursor()

res = curs.execute('''
    UPDATE houses
    SET street = 'Клары Цеткин', number = 52
    WHERE number = 5
''')

connection.commit()
connection.close()

