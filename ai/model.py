def build_cnn(size=96, seed=42):
    import tensorflow as tf
    from tensorflow.keras import layers, Sequential
    tf.keras.utils.set_random_seed(seed)
    # Tat ca trong so khoi tao ngau nhien. Khong goi applications/pretrained.
    return Sequential([
        layers.Input(shape=(size,size,3)),
        layers.RandomFlip('horizontal',seed=seed),
        layers.RandomRotation(0.035,seed=seed+1),
        layers.RandomZoom(0.10,seed=seed+2),
        layers.RandomContrast(0.15,seed=seed+3),
        layers.RandomBrightness(0.15,value_range=(0,255),seed=seed+4),
        layers.Rescaling(1./255),
        layers.Conv2D(16,3,padding='same',activation='relu'),
        layers.MaxPooling2D(),
        layers.Conv2D(32,3,padding='same',activation='relu'),
        layers.MaxPooling2D(),
        layers.Conv2D(64,3,padding='same',activation='relu'),
        layers.MaxPooling2D(),
        layers.Flatten(),
        layers.Dense(64,activation='relu'),
        layers.Dropout(0.4,seed=seed+5),
        layers.Dense(1,activation='sigmoid'),
    ], name='parking_cnn_from_scratch')
