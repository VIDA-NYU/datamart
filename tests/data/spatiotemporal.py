from datetime import datetime, timedelta
import random
import os


GRID_CELL_SIZE = 0.001
COLORS = ['red', 'green', 'blue', 'yellow', 'orange']


def main():
    lat = 43.237597
    lon = 6.072545

    data_dir = os.path.dirname(__file__)

    with open(os.path.join(data_dir, 'spatiotemporal.csv'), 'w') as f_data:
        print('date,latitude,longitude,color', file=f_data)
        rand = random.Random(1)
        for t in range(20):
            time = datetime(2006, 6, 20)
            time += timedelta(minutes=t * 30)
            for _ in range(10):
                print(
                    '%s,%.3f,%.3f,%s' % (
                        time.isoformat(),
                        lat + GRID_CELL_SIZE * (rand.random() * 6 - 3),
                        lon + GRID_CELL_SIZE * (rand.random() * 6 - 3),
                        rand.choice(COLORS),
                    ),
                    file=f_data,
                )

    with open(os.path.join(data_dir, 'spatiotemporal_aug.csv'), 'w') as f_data:
        print('date,latitude,longitude', file=f_data)
        for t in range(3):
            time = datetime(2006, 6, 20, 6)
            time += timedelta(hours=t)
            for x in range(-1, 1):
                for y in range(-1, 1):
                    print(
                        '%s,%.3f,%.3f' % (
                            time.isoformat(),
                            lat + GRID_CELL_SIZE * y,
                            lon + GRID_CELL_SIZE * x,
                        ),
                        file=f_data,
                    )


if __name__ == '__main__':
    main()
